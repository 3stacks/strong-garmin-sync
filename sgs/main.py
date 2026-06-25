"""Orchestrator: fetch new Strong workouts, merge each into its matching Garmin
activity (or standalone-import), upload, delete the original. Idempotent via state."""
from __future__ import annotations

import argparse
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fit_merge
from .config import Config
from .garmin_client import GarminClient
from .matcher import match
from .state import State
from .strong_client import StrongClient

log = logging.getLogger("sgs")


def run_once(cfg: Config) -> None:
    state = State(cfg.state_path)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=cfg.lookback_days)

    strong = StrongClient(cfg)
    workouts = [w for w in strong.fetch_workouts(since=since) if not state.processed(w.id)]
    if not workouts:
        log.info("nothing new to sync")
        return

    garmin = GarminClient(cfg)
    garmin.login()
    activities = garmin.strength_activities(since.date(), now.date())

    ok = fail = 0
    for w in workouts:
        try:
            _process(w, garmin, activities, state, cfg)
            ok += 1
        except NotImplementedError as e:
            fail += 1
            log.error("FIT merge not wired yet for %s: %s", w.id[:8], e)
        except Exception:  # noqa: BLE001 - one bad workout shouldn't kill the batch
            fail += 1
            log.exception("failed to sync %s", w)
    log.info("done: %d synced, %d failed, %d skipped(existing)", ok, fail,
             len(workouts) - ok - fail)


def _process(w, garmin: GarminClient, activities: list[dict], state: State, cfg: Config):
    log.info("processing %s", w)
    activity = match(w, activities, cfg.match_window_s)
    watch_fit = garmin.download_original_fit(activity["activityId"]) if activity else None

    merged = fit_merge.build_merged_fit(w, watch_fit, cfg)

    with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tf:
        tf.write(merged)
        path = tf.name
    try:
        resp = garmin.upload_fit(path)
        new_id = _new_activity_id(resp)
        replaced = activity["activityId"] if activity else None
        # only delete the original AFTER a successful upload, and never in dry-run
        if replaced and not cfg.dry_run:
            garmin.delete_activity(replaced)
        state.record(w.id, garmin_activity_id=new_id, replaced_activity_id=replaced,
                     uploaded_at=datetime.now(timezone.utc).isoformat(),
                     note="standalone-import" if not activity else "merged")
    finally:
        Path(path).unlink(missing_ok=True)


def _new_activity_id(resp):
    # upload_activity returns a dict; shape varies by lib version — best-effort.
    try:
        d = resp if isinstance(resp, dict) else {}
        detail = d.get("detailedImportResult") or {}
        succ = detail.get("successes") or []
        return succ[0].get("internalId") if succ else None
    except (AttributeError, IndexError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser(description="Mirror Strong workouts into Garmin Connect.")
    ap.add_argument("--once", action="store_true", help="run a single sync pass")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = Config.load()
    if cfg.dry_run:
        log.info("DRY_RUN: will fetch/match/build but not upload or delete")
    run_once(cfg)


if __name__ == "__main__":
    main()
