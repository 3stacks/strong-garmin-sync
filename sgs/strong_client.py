"""Client for Strong's private sync API.

Contract reverse-engineered from tolik518/strong-api-workout-sync (strong-api-lib).
See docs/strong-api-contract.md. Only the base host is unknown (captured once).

Endpoints used:
  POST  {base}auth/login            {usernameOrEmail, password} -> {accessToken, refreshToken, userId}
  POST  {base}auth/login/refresh    {accessToken, refreshToken} -> {accessToken, refreshToken}   (Bearer)
  GET   {base}api/users/{userId}?continuation=&limit=&includes=log&includes=measurement   (Bearer)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from dateutil import parser as dtparser

from .config import Config
from .models import StrongExercise, StrongSet, StrongWorkout

log = logging.getLogger("sgs.strong")

# Real Strong cellTypes (UPPERCASE on the wire; lowercased before classify):
#   *_WEIGHT (BARBELL/DUMBBELL/OTHER/MACHINE/...), WEIGHTED_BODYWEIGHT, ASSISTED_BODYWEIGHT,
#   REPS, DURATION, RPE. cellSetTag carries WARM_UP/DROP_SET/FAILURE (not used yet).
_seen_celltypes: set[str] = set()


def _classify_cell(ctype: str) -> str | None:
    if ctype == "reps":
        return "reps"
    if ctype == "duration":
        return "duration"
    if ctype == "rpe":
        return "rpe"
    if ctype == "assisted_bodyweight":
        return "assist"
    if ctype in ("weight", "weighted_bodyweight") or ctype.endswith("_weight"):
        return "weight"
    return None


class StrongClient:
    def __init__(self, cfg: Config, token_path: Path | None = None):
        self.cfg = cfg
        self.s = requests.Session()
        self.s.headers.update({
            "user-agent": "Strong Android",
            "content-type": "application/json",
            "accept": "application/json",
            "x-client-build": cfg.strong_client_build,
            "x-client-platform": "android",
        })
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.user_id: str | None = None
        self._token_path = token_path or (cfg.state_path.parent / ".strong_tokens.json")

    # ---- auth -------------------------------------------------------------
    def _url(self, path: str) -> str:
        return urljoin(self.cfg.strong_backend, path)

    def _store_tokens(self):
        try:
            self._token_path.write_text(json.dumps({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "user_id": self.user_id,
            }))
            self._token_path.chmod(0o600)
        except OSError as e:
            log.warning("could not persist Strong tokens: %s", e)

    def _load_tokens(self) -> bool:
        if not self._token_path.exists():
            return False
        try:
            d = json.loads(self._token_path.read_text())
            self.access_token = d.get("access_token")
            self.refresh_token = d.get("refresh_token")
            self.user_id = d.get("user_id")
            return bool(self.access_token and self.user_id)
        except (OSError, json.JSONDecodeError):
            return False

    def login(self):
        r = self.s.post(self._url("auth/login"), json={
            "usernameOrEmail": self.cfg.strong_user,
            "password": self.cfg.strong_pass,
        }, timeout=30)
        r.raise_for_status()
        d = r.json()
        self.access_token = d.get("accessToken")
        self.refresh_token = d.get("refreshToken")
        self.user_id = d.get("userId")
        if not (self.access_token and self.user_id):
            raise RuntimeError(f"login returned unexpected shape: keys={list(d)}")
        self._store_tokens()
        log.info("logged in to Strong as user %s", self.user_id)

    def refresh(self) -> bool:
        if not (self.access_token and self.refresh_token):
            return False
        r = self.s.post(self._url("auth/login/refresh"),
                        headers={"authorization": f"Bearer {self.access_token}"},
                        json={"accessToken": self.access_token, "refreshToken": self.refresh_token},
                        timeout=30)
        if r.status_code >= 400:
            log.info("token refresh failed (%s); will re-login", r.status_code)
            return False
        d = r.json()
        self.access_token = d.get("accessToken")
        self.refresh_token = d.get("refreshToken")
        self._store_tokens()
        return True

    def ensure_auth(self):
        """Reuse stored tokens (refresh), else full login. Keeps the cron gentle on /auth/login."""
        if self._load_tokens() and self.refresh():
            log.debug("reused Strong tokens")
            return
        self.login()

    # ---- fetch ------------------------------------------------------------
    def _get_users_page(self, continuation: str, limit: int) -> dict:
        r = self.s.get(self._url(f"api/users/{self.user_id}"),
                       headers={"authorization": f"Bearer {self.access_token}"},
                       params=[("limit", str(limit)), ("continuation", continuation),
                               ("include", "log"), ("include", "measurement")],
                       timeout=60)
        if r.status_code == 401:  # token died mid-run
            self.login()
            return self._get_users_page(continuation, limit)
        r.raise_for_status()
        return r.json()

    def fetch_measurements(self, max_pages: int = 20) -> dict[str, str]:
        """Full exercise catalog (api/measurements, paginated): measurement id -> name.
        The user-endpoint's embedded measurements are incomplete, so we load the catalog."""
        idx: dict[str, str] = {}
        total = None
        for page in range(max_pages):
            r = self.s.get(self._url("api/measurements"),
                           headers={"authorization": f"Bearer {self.access_token}"},
                           params={"page": str(page)}, timeout=60)
            if r.status_code == 401:
                self.login()
                r = self.s.get(self._url("api/measurements"),
                               headers={"authorization": f"Bearer {self.access_token}"},
                               params={"page": str(page)}, timeout=60)
            r.raise_for_status()
            d = r.json()
            total = d.get("total", total)
            items = (d.get("_embedded", {}) or {}).get("measurement") or []
            if not items:
                break
            idx.update(self._index_measurements(items))
            if total is not None and len(idx) >= total:
                break
        log.info("loaded %d Strong exercise names", len(idx))
        return idx

    def fetch_workouts(self, since: datetime | None = None, limit: int = 100,
                       max_pages: int = 100) -> list[StrongWorkout]:
        """Flatten logs to StrongWorkout, keeping those with start >= `since`.

        Strong returns logs OLDEST-first via an opaque forward cursor (`_links.next`),
        and honours no sort param — so to reach recent workouts we page forward to the
        end and filter. (Cursor-resume to avoid re-paging history every run is a TODO.)"""
        self.ensure_auth()
        measurements = self.fetch_measurements()
        out: list[StrongWorkout] = []
        continuation = ""
        seen_cursors: set[str] = set()
        pages = 0
        for _ in range(max_pages):
            payload = self._get_users_page(continuation, limit)
            embedded = payload.get("_embedded", {}) or {}
            measurements.update(self._index_measurements(embedded.get("measurement") or []))
            logs = embedded.get("log") or []
            if not logs:
                break
            pages += 1
            for lg in logs:
                w = self._parse_log(lg, measurements)
                if w and (since is None or w.start >= since):
                    out.append(w)
            nxt = _extract_continuation((payload.get("_links", {}) or {}).get("next", {}))
            if not nxt or nxt in seen_cursors:
                break
            seen_cursors.add(nxt)
            continuation = nxt
        out.sort(key=lambda w: w.start)
        log.info("fetched %d Strong workouts (>= since) across %d pages", len(out), pages)
        return out

    # ---- parsing ----------------------------------------------------------
    @staticmethod
    def _index_measurements(items: list[dict]) -> dict[str, str]:
        """measurement id -> display name (exercise definitions live here)."""
        idx: dict[str, str] = {}
        for m in items:
            mid = m.get("id")
            name = _name_of(m.get("name"))
            if mid and name:
                idx[mid] = name
        return idx

    def _parse_log(self, lg: dict, measurements: dict[str, str]) -> StrongWorkout | None:
        try:
            start = _dt(lg.get("startDate"))
            if start is None:
                return None
            exercises = []
            for grp in (lg.get("_embedded", {}) or {}).get("cellSetGroup", []) or []:
                ex = self._parse_group(grp, measurements)
                if ex and ex.sets:
                    exercises.append(ex)
            return StrongWorkout(
                id=lg["id"],
                name=_name_of(lg.get("name")) or "Workout",
                start=start,
                end=_dt(lg.get("endDate")),
                exercises=exercises,
            )
        except (KeyError, TypeError) as e:
            log.warning("skipping malformed log %s: %s", lg.get("id"), e)
            return None

    def _parse_group(self, grp: dict, measurements: dict[str, str]) -> StrongExercise | None:
        mid = _measurement_id(grp)
        name = measurements.get(mid, "") if mid else ""
        sets = [s for cs in grp.get("cellSets", []) if (s := self._parse_set(cs))]
        return StrongExercise(id=mid or grp.get("id", ""), name=name or "Exercise", sets=sets)

    @staticmethod
    def _parse_set(cell_set: dict) -> StrongSet | None:
        reps = weight = rpe = duration = None
        assisted = False
        for cell in cell_set.get("cells", []) or []:
            ctype = str(cell.get("cellType", "")).lower()
            val = cell.get("value")
            if ctype not in _seen_celltypes:
                _seen_celltypes.add(ctype)
                log.debug("Strong cellType seen: %r (value=%r)", ctype, val)
            if val in (None, ""):
                continue
            kind = _classify_cell(ctype)
            if kind == "reps":
                reps = _to_int(val)
            elif kind == "weight":
                weight = _to_float(val)
            elif kind == "assist":
                weight = _to_float(val)
                assisted = True
            elif kind == "duration":
                duration = _to_float(val)
            elif kind == "rpe":
                rpe = _to_float(val)
        if reps is None and weight is None and duration is None:
            return None
        return StrongSet(reps=reps or 0, weight=weight, duration=duration, rpe=rpe,
                         assisted=assisted, is_completed=bool(cell_set.get("isCompleted", True)))


# ---- small helpers --------------------------------------------------------
def _name_of(name) -> str:
    """Strong `Name` is a localized object like {"en": "Squat (Barbell)"} (or a string)."""
    if isinstance(name, str):
        return name
    if isinstance(name, dict):
        for k in ("en", "value", "default"):
            if name.get(k):
                return name[k]
        for v in name.values():
            if isinstance(v, str) and v:
                return v
    return ""


def _measurement_id(grp: dict) -> str | None:
    href = (((grp.get("_links") or {}).get("measurement")) or {}).get("href")
    if not href:
        return None
    return href.rstrip("/").rsplit("/", 1)[-1]


def _extract_continuation(next_link: dict) -> str:
    href = (next_link or {}).get("href", "")
    if "continuation=" in href:
        return href.split("continuation=", 1)[1].split("&", 1)[0]
    return ""


def _dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return dtparser.isoparse(s)
    except (ValueError, TypeError):
        return None


def _to_int(v):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
