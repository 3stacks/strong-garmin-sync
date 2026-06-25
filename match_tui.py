"""Interactive TUI: match a Strong workout to a Garmin activity and enrich it in place.

    python match_tui.py                # last 30 days
    python match_tui.py --days 90

Left pane = Strong workouts, right pane = Garmin strength activities.
Tab switches panes · ↑/↓ move · selecting a Strong workout auto-highlights the closest
Garmin activity · [m] enrich the cursored pair · [u] restore the cursored activity ·
[r] reload · [q] quit. Already-matched rows are green with a ✓.
"""
from __future__ import annotations

import os
import sys

# Re-launch under the project venv so `python3 match_tui.py` finds deps (textual, etc.).
# NB: compare unresolved paths — a venv's python is a symlink to the base interpreter,
# so realpath() would make them equal and skip the re-exec.
_VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
if os.path.exists(_VENV_PY) and os.path.normpath(sys.executable) != os.path.normpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Static

from sgs import enrich
from sgs.config import Config
from sgs.garmin_client import GarminClient, activity_start
from sgs.state import State
from sgs.strong_client import StrongClient

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
_MIN = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class Data:
    workouts: list = field(default_factory=list)     # StrongWorkout, newest first
    activities: list = field(default_factory=list)    # garmin dicts, newest first
    matched_strong: set = field(default_factory=set)
    matched_garmin: set = field(default_factory=set)  # activity ids (str)
    link: dict = field(default_factory=dict)          # strong id -> garmin id


def load_data(cfg: Config, gc: GarminClient, days: int) -> Data:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    workouts = StrongClient(cfg).fetch_workouts(since=since)
    workouts.sort(key=lambda w: w.start, reverse=True)
    acts = gc.strength_activities(since.date(), datetime.now(timezone.utc).date())
    acts.sort(key=lambda a: activity_start(a) or _MIN, reverse=True)

    state = State(cfg.state_path)
    matched_strong, matched_garmin, link = set(), set(), {}
    for sid, rec in state.items().items():
        matched_strong.add(sid)
        gid = rec.get("garmin_activity_id")
        if gid:
            matched_garmin.add(str(gid))
            link[sid] = str(gid)

    # Also detect activities already enriched outside the state file (reps present).
    for a in acts:
        aid = str(a.get("activityId"))
        try:
            es = gc.api.get_activity_exercise_sets(int(aid))
        except Exception:  # noqa: BLE001
            continue
        if not enrich.is_enriched(es):
            continue
        matched_garmin.add(aid)
        astart = activity_start(a)
        if not astart:
            continue
        best, bestd = None, None
        for w in workouts:
            d = abs((astart - w.start).total_seconds())
            if d <= cfg.match_window_s and (bestd is None or d < bestd):
                best, bestd = w, d
        if best:
            matched_strong.add(best.id)
            link.setdefault(best.id, aid)
    return Data(workouts, acts, matched_strong, matched_garmin, link)


class MatchApp(App):
    CSS = """
    Horizontal { height: 1fr; }
    DataTable { width: 1fr; }
    #status { height: 3; padding: 0 1; background: $panel; color: $text; }
    """
    BINDINGS = [
        ("m", "match", "Match→enrich"),
        ("u", "unmatch", "Restore"),
        ("r", "reload", "Reload"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, cfg: Config, days: int):
        super().__init__()
        self.cfg = cfg
        self.days = days
        self.gc = GarminClient(cfg)
        self.state = State(cfg.state_path)
        self.data = Data()
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id="strong", cursor_type="row", zebra_stripes=True)
            yield DataTable(id="garmin", cursor_type="row", zebra_stripes=True)
        yield Static("Loading…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Strong → Garmin matcher"
        st = self.query_one("#strong", DataTable)
        st.add_columns(" ", "When", "Workout", "ex", "sets")
        st.border_title = "Strong workouts"
        gt = self.query_one("#garmin", DataTable)
        gt.add_columns(" ", "When", "Activity", "dur")
        gt.border_title = "Garmin strength activities"
        st.focus()
        self.reload()

    # ---- data ----
    @work(thread=True)
    def reload(self) -> None:
        self.call_from_thread(self._set_status, "Loading Strong + Garmin (paging history)…")
        try:
            self.gc.login()
            data = load_data(self.cfg, self.gc, self.days)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._set_status, f"load failed: {e}")
            return
        self.data = data
        self.call_from_thread(self._populate)

    def _populate(self) -> None:
        st = self.query_one("#strong", DataTable)
        gt = self.query_one("#garmin", DataTable)
        st.clear()
        gt.clear()
        for w in self.data.workouts:
            st.add_row(*self._strong_cells(w, w.id in self.data.matched_strong), key=w.id)
        for a in self.data.activities:
            aid = str(a.get("activityId"))
            gt.add_row(*self._garmin_cells(a, aid in self.data.matched_garmin), key=aid)
        self._update_status()

    @staticmethod
    def _c(s: str, matched: bool) -> Text:
        return Text(s, style="bold green" if matched else "")

    def _strong_cells(self, w, matched):
        when = w.start.astimezone().strftime("%a %m-%d %H:%M")
        return (self._c("✓" if matched else "·", matched), self._c(when, matched),
                self._c(w.name, matched), self._c(str(len(w.exercises)), matched),
                self._c(str(w.total_sets), matched))

    def _garmin_cells(self, a, matched):
        astart = activity_start(a)
        when = astart.astimezone().strftime("%a %m-%d %H:%M") if astart else "?"
        dur = f"{(a.get('duration') or 0) / 60:.0f}m"
        name = str(a.get("activityName") or "Strength")
        return (self._c("✓" if matched else "·", matched), self._c(when, matched),
                self._c(name, matched), self._c(dur, matched))

    # ---- cursor helpers ----
    def _cur_strong(self):
        i = self.query_one("#strong", DataTable).cursor_row
        return self.data.workouts[i] if 0 <= i < len(self.data.workouts) else None

    def _cur_garmin(self):
        i = self.query_one("#garmin", DataTable).cursor_row
        return self.data.activities[i] if 0 <= i < len(self.data.activities) else None

    def _closest_garmin_idx(self, w):
        best, bestd = None, None
        for i, a in enumerate(self.data.activities):
            astart = activity_start(a)
            if not astart:
                continue
            d = abs((astart - w.start).total_seconds())
            if bestd is None or d < bestd:
                best, bestd = i, d
        return best if (bestd is not None and bestd <= self.cfg.match_window_s) else None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.busy or not self.data.workouts:
            return
        if event.data_table.id == "strong":
            w = self._cur_strong()
            if w is not None:
                idx = self._closest_garmin_idx(w)
                if idx is not None:
                    self.query_one("#garmin", DataTable).move_cursor(row=idx)
        self._update_status()

    def _update_status(self) -> None:
        w, a = self._cur_strong(), self._cur_garmin()
        if not w or not a:
            self._set_status("pick a Strong workout (left) and a Garmin activity (right)")
            return
        astart = activity_start(a)
        d = abs((astart - w.start).total_seconds()) if astart else None
        dtxt = f"Δ{d / 60:.0f}m" if d is not None else "Δ?"
        warn = "  ⚠ far apart" if (d is not None and d > self.cfg.match_window_s) else ""
        done = "  [already matched]" if (w.id in self.data.matched_strong
                                         or str(a.get("activityId")) in self.data.matched_garmin) else ""
        self._set_status(
            f"[m] {w.name} {w.start.astimezone():%m-%d %H:%M}  ↔  "
            f"{a.get('activityName') or 'Strength'} {astart.astimezone():%m-%d %H:%M}  {dtxt}{warn}{done}")

    def _set_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    # ---- actions ----
    def action_match(self) -> None:
        if self.busy:
            return
        w, a = self._cur_strong(), self._cur_garmin()
        if not w or not a:
            self._set_status("select a workout and an activity first")
            return
        self.busy = True
        self._do_match(w, str(a.get("activityId")))

    @work(thread=True)
    def _do_match(self, w, aid: str) -> None:
        self.call_from_thread(self._set_status, f"enriching activity {aid}…")
        try:
            summ = enrich.enrich_pair(self.gc.api, aid, w, self.cfg.strong_weight_unit,
                                      BACKUP_DIR, state=self.state)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._finish, f"enrich failed: {e}")
            return
        self.data.matched_strong.add(w.id)
        self.data.matched_garmin.add(aid)
        self.data.link[w.id] = aid
        self.call_from_thread(self._finish, f"✓ {w.name} → {aid}  ({summ.get('mode')}, "
                                            f"{summ.get('strong_sets')} sets)")

    def action_unmatch(self) -> None:
        if self.busy:
            return
        a = self._cur_garmin()
        if not a:
            return
        self.busy = True
        self._do_restore(str(a.get("activityId")))

    @work(thread=True)
    def _do_restore(self, aid: str) -> None:
        self.call_from_thread(self._set_status, f"restoring activity {aid}…")
        try:
            ok = enrich.restore_activity(self.gc.api, aid, BACKUP_DIR)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self._finish, f"restore failed: {e}")
            return
        for sid in [s for s, g in self.data.link.items() if g == aid]:
            self.data.matched_strong.discard(sid)
            self.data.link.pop(sid, None)
            self.state.remove(sid)
        self.data.matched_garmin.discard(aid)
        self.call_from_thread(self._finish, f"{'restored' if ok else 'no backup for'} {aid}")

    def _finish(self, msg: str) -> None:
        self.busy = False
        self._populate()
        self._set_status(msg)

    def action_reload(self) -> None:
        if not self.busy:
            self.reload()


def main():
    ap = argparse.ArgumentParser(description="Match Strong workouts to Garmin activities.")
    ap.add_argument("--days", type=int, default=30, help="lookback window (default 30)")
    args = ap.parse_args()
    MatchApp(Config.load(), args.days).run()


if __name__ == "__main__":
    main()
