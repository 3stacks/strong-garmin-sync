"""Enrich ONE Garmin activity from its matching Strong workout.
Backs up first; dry-run by default, --put to write. Restore via garmin_unit_test.py --restore.

    python enrich_activity.py [activity_id]          # dry-run
    python enrich_activity.py [activity_id] --put    # write
"""
import os
import sys

_VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
if os.path.exists(_VENV_PY) and os.path.normpath(sys.executable) != os.path.normpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sgs import enrich
from sgs.config import Config
from sgs.garmin_client import GarminClient, activity_start
from sgs.strong_client import StrongClient

ACT = next((a for a in sys.argv[1:] if a.isdigit()), None)
PUT = "--put" in sys.argv
if not ACT:
    sys.exit("usage: python enrich_activity.py <garmin_activity_id> [--put]")
cfg = Config.load()

since = datetime.now(timezone.utc) - timedelta(days=14)
workouts = StrongClient(cfg).fetch_workouts(since=since)
g = GarminClient(cfg)
g.login()
acts = g.strength_activities(since.date(), datetime.now(timezone.utc).date())

act = next((a for a in acts if str(a.get("activityId")) == ACT), None)
if not act:
    sys.exit(f"activity {ACT} not in recent strength activities")
astart = activity_start(act)
best, bestd = None, None
for w in workouts:
    d = abs((astart - w.start).total_seconds())
    if d <= cfg.match_window_s and (bestd is None or d < bestd):
        best, bestd = w, d
if not best:
    sys.exit("no matching Strong workout within window")
print(f"Garmin {ACT} ({astart.isoformat()})  <->  Strong {best.name!r} ({best.start.isoformat()})  Δ{int(bestd)}s")

es = g.api.get_activity_exercise_sets(int(ACT))
backup = Path(__file__).resolve().parent / "backups" / f"exercise_sets_backup_{ACT}.json"
backup.parent.mkdir(parents=True, exist_ok=True)
if backup.exists():
    print(f"backup already exists (original preserved): {backup}")
else:
    backup.write_text(json.dumps(es, indent=2, default=str))
    print(f"backed up original to {backup}")

payload, summ = enrich.build_payload(es, best, cfg.strong_weight_unit)
print("summary:", summ)

if not PUT:
    print("\nDRY-RUN (add --put to write). ACTIVE sets that would be set:")
    for s in payload["exerciseSets"]:
        if s.get("setType") == "ACTIVE" and s.get("repetitionCount"):
            print(f"   reps={s['repetitionCount']} weight={s.get('weight')}g exercises={s.get('exercises')}")
    sys.exit(0)

try:
    g.api.set_activity_exercise_sets(int(ACT), payload)
except Exception as e:  # noqa: BLE001 - bad sub-category name -> fall back to category-only
    print(f"PUT with names failed ({str(e)[:90]}); retrying category-only")
    payload, summ = enrich.build_payload(es, best, cfg.strong_weight_unit, use_names=False)
    g.api.set_activity_exercise_sets(int(ACT), payload)
print("PUT ok")
back = g.api.get_activity_exercise_sets(int(ACT))
filled = [s for s in (back.get("exerciseSets") or [])
          if s.get("setType") == "ACTIVE" and s.get("repetitionCount")]
print(f"read-back: {len(filled)} ACTIVE sets now carry reps/weight")
print(f">>> Verify in Garmin Connect -> the {astart.date()} 'Strength' -> Sets. "
      f"Restore anytime: python garmin_unit_test.py {ACT} --restore")
