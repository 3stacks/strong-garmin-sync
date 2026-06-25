"""READ-ONLY Garmin inspection: confirm login, list recent strength activities, and
dump one activity's exerciseSets payload shape. No writes."""
import os
import sys

_VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
if os.path.exists(_VENV_PY) and os.path.normpath(sys.executable) != os.path.normpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])

import json
import logging
from datetime import date, timedelta

from garminconnect import Garmin

from sgs.config import Config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("inspect")

cfg = Config.load()
tokenstore = str(cfg.garmin_token_dir)

api = None
try:
    g = Garmin()
    g.login(tokenstore)
    api = g
    log.info("resumed from cached tokens")
except Exception as e:  # noqa: BLE001
    log.info("no cached tokens (%s); credential login", type(e).__name__)

if api is None:
    g = Garmin(email=cfg.garmin_user, password=cfg.garmin_pass, return_on_mfa=True)
    res = g.login(tokenstore)
    if isinstance(res, tuple) and res and res[0] == "needs_mfa":
        print("\nMFA_REQUIRED — your Garmin account needs a one-time code.")
        print("Run this once in your terminal:")
        print("    source .venv/bin/activate && python garmin_login.py")
        raise SystemExit(0)
    api = g
    try:
        g.garth.dump(tokenstore)
        log.info("cached tokens to %s", tokenstore)
    except Exception as e:  # noqa: BLE001
        log.warning("token dump failed: %s", e)

end = date.today()
start = end - timedelta(days=120)
acts = api.get_activities_by_date(start.isoformat(), end.isoformat())
strength = [a for a in acts if "strength" in str((a.get("activityType") or {}).get("typeKey", "")).lower()]
print(f"\nstrength activities in last 120d: {len(strength)} (of {len(acts)} total)")
for a in strength[:10]:
    print(f"  id={a.get('activityId')} start={a.get('startTimeLocal')} "
          f"name={a.get('activityName')!r} dur={a.get('duration')}")

if strength:
    aid = strength[0]["activityId"]
    print(f"\n--- get_activity_exercise_sets({aid}) ---")
    es = api.get_activity_exercise_sets(aid)
    print("top keys:", list(es))
    sets = es.get("exerciseSets") or es.get("activityExerciseSets") or []
    print("num exerciseSets:", len(sets))
    print(json.dumps(sets[:3], indent=2, default=str)[:2000])
else:
    print("\n(no strength activities in range — widen the window or record one on the watch)")
