"""Thin wrapper over python-garminconnect (cyberjunky).

Auth: mobile SSO flow with token cache at GARMIN_TOKEN_DIR, so MFA is a one-time
prompt at first login; later runs resume silently. See docs/strong-api-contract.md.

NOTE: garminconnect's method names drift between releases. The wrappers below target a
recent version; if a call fails, check the installed lib's API and adjust here only.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime

from garminconnect import Garmin

from .config import Config

log = logging.getLogger("sgs.garmin")


def _default_mfa_prompt() -> str:
    return input("Garmin MFA code: ").strip()


class GarminClient:
    def __init__(self, cfg: Config, mfa_prompt=_default_mfa_prompt):
        self.cfg = cfg
        self._mfa_prompt = mfa_prompt
        self.api: Garmin | None = None

    def login(self):
        tokenstore = str(self.cfg.garmin_token_dir)
        self.cfg.garmin_token_dir.mkdir(parents=True, exist_ok=True)
        # Prefer resuming cached tokens (no MFA, avoids Garmin login rate-limits/429).
        # The resume attempt has no creds, so it fails locally before any network call.
        try:
            api = Garmin()
            api.login(tokenstore)
            self.api = api
            log.info("resumed Garmin session from %s", tokenstore)
            return
        except Exception as e:  # noqa: BLE001 - cache absent/expired
            log.info("no usable Garmin token cache (%s); credential login", type(e).__name__)
        api = Garmin(email=self.cfg.garmin_user, password=self.cfg.garmin_pass,
                     prompt_mfa=self._mfa_prompt)
        api.login(tokenstore)   # library persists tokens to tokenstore on success
        self.api = api
        log.info("logged in to Garmin (tokens cached at %s)", tokenstore)

    # ---- reads ------------------------------------------------------------
    def strength_activities(self, start: date, end: date) -> list[dict]:
        """Strength-training activities in [start, end] (inclusive), newest first."""
        assert self.api
        acts = self.api.get_activities_by_date(start.isoformat(), end.isoformat())
        out = [a for a in acts if "strength" in _type_key(a)]
        log.info("found %d Garmin strength activities in %s..%s", len(out), start, end)
        return out

    def download_original_fit(self, activity_id) -> bytes:
        """Original upload as a .fit (Garmin returns a zip for ORIGINAL)."""
        assert self.api
        data = self.api.download_activity(
            activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
        if data[:2] == b"PK":  # zip
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
                if not fit_names:
                    raise RuntimeError(f"no .fit inside ORIGINAL zip for {activity_id}: {zf.namelist()}")
                return zf.read(fit_names[0])
        return data  # already a bare .fit

    # ---- writes -----------------------------------------------------------
    def upload_fit(self, path: str) -> object:
        assert self.api
        if self.cfg.dry_run:
            log.info("[dry-run] would upload %s", path)
            return {"dry_run": True}
        return self.api.upload_activity(path)

    def delete_activity(self, activity_id) -> None:
        assert self.api
        if self.cfg.dry_run:
            log.info("[dry-run] would delete activity %s", activity_id)
            return
        self.api.delete_activity(activity_id)
        log.info("deleted original Garmin activity %s", activity_id)


def _type_key(activity: dict) -> str:
    return str((activity.get("activityType") or {}).get("typeKey", "")).lower()


def activity_start(activity: dict) -> datetime | None:
    """Best-effort tz-aware start time of a Garmin activity (prefers GMT)."""
    from dateutil import parser as dtparser
    gmt = activity.get("startTimeGMT")
    if gmt:
        try:
            dt = dtparser.isoparse(gmt)
            from datetime import timezone
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except (ValueError, TypeError):
            pass
    return None
