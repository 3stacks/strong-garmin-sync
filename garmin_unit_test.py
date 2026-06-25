"""Reversible weight-unit probe: back up an activity's exerciseSets, set two ACTIVE sets
to distinguishable (reps, weight) encodings, PUT, read back. Restore with --restore.

    python garmin_unit_test.py [activity_id]      # write the two probes
    python garmin_unit_test.py [activity_id] --restore   # put the backup back
"""
import os
import sys

_VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
if os.path.exists(_VENV_PY) and os.path.normpath(sys.executable) != os.path.normpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])

import json
from pathlib import Path

from sgs.config import Config
from sgs.garmin_client import GarminClient

ACT = next((a for a in sys.argv[1:] if a.isdigit()), None)
RESTORE = "--restore" in sys.argv
if not ACT:
    sys.exit("usage: python garmin_unit_test.py <garmin_activity_id> [--restore]")
BACKUP = Path(__file__).resolve().parent / "backups" / f"exercise_sets_backup_{ACT}.json"

cfg = Config.load()
gc = GarminClient(cfg)
gc.login()
api = gc.api

if RESTORE:
    if not BACKUP.exists():
        sys.exit(f"no backup at {BACKUP}")
    original = json.loads(BACKUP.read_text())
    api.set_activity_exercise_sets(int(ACT), original)
    print(f"restored original exerciseSets for {ACT} from {BACKUP}")
    sys.exit(0)

es = api.get_activity_exercise_sets(int(ACT))
BACKUP.parent.mkdir(parents=True, exist_ok=True)
BACKUP.write_text(json.dumps(es, indent=2, default=str))
print(f"backed up original to {BACKUP}")

sets = es.get("exerciseSets") or []
active = [s for s in sets if s.get("setType") == "ACTIVE" and 5 <= (s.get("duration") or 0) <= 300]
print(f"total sets={len(sets)}  usable ACTIVE sets={len(active)}")
if len(active) < 2:
    sys.exit("need >=2 usable ACTIVE sets; aborting")

# probe A: grams hypothesis (60000 -> 60 kg)
active[0]["exercises"] = [{"category": "BENCH_PRESS", "name": None}]
active[0]["repetitionCount"] = 5
active[0]["weight"] = 60000
# probe B: kg-units hypothesis (60 -> 60 kg)
active[1]["exercises"] = [{"category": "BENCH_PRESS", "name": None}]
active[1]["repetitionCount"] = 7
active[1]["weight"] = 60

api.set_activity_exercise_sets(int(ACT), es)
print("PUT ok")

back = api.get_activity_exercise_sets(int(ACT))
for s in (back.get("exerciseSets") or []):
    if s.get("repetitionCount") in (5, 7):
        print(f"  read-back: reps={s.get('repetitionCount')} weight={s.get('weight')} "
              f"exercises={s.get('exercises')}")

print("\n>>> Open Garmin Connect -> today's 'Strength' activity. Find the 5-rep set and the "
      "7-rep set; tell me the WEIGHT each shows (and your display unit kg/lb).")
