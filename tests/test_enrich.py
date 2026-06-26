"""Self-test of enrich.build_payload's FILL/REPLACE selection and the slot<->set
alignment that the Upper B mislabel exposed. Pure, no network.

    python tests/test_enrich.py
"""
from datetime import datetime, timezone, timedelta

from sgs import enrich
from sgs.models import StrongWorkout, StrongExercise, StrongSet

UNIT = "kg"
START = datetime(2026, 6, 26, 3, 20, 0, tzinfo=timezone.utc)


def watch_sets(n_active, active_dur=120.0, rest_dur=60.0):
    """A watch exerciseSets dict: n_active ACTIVE slots (UNKNOWN, no reps), each followed
    by a REST slot — mirroring real Garmin auto-detected strength data."""
    sets, t, mi = [], START, 0
    for _ in range(n_active):
        sets.append({"exercises": [{"category": "UNKNOWN", "name": None}],
                     "setType": "ACTIVE", "repetitionCount": None, "weight": None,
                     "duration": active_dur,
                     "startTime": t.strftime("%Y-%m-%dT%H:%M:%S.0"), "messageIndex": mi})
        t += timedelta(seconds=active_dur); mi += 1
        sets.append({"exercises": [], "setType": "REST", "repetitionCount": None,
                     "weight": None, "duration": rest_dur,
                     "startTime": t.strftime("%Y-%m-%dT%H:%M:%S.0"), "messageIndex": mi})
        t += timedelta(seconds=rest_dur); mi += 1
    return {"activityId": 1, "exerciseSets": sets}


def active_view(payload):
    """[(category, name, reps, weight)] for ACTIVE sets carrying reps, in order."""
    out = []
    for s in payload["exerciseSets"]:
        if s.get("setType") == "ACTIVE" and s.get("repetitionCount"):
            e0 = (s.get("exercises") or [{}])[0]
            out.append((e0.get("category"), e0.get("name"),
                        s.get("repetitionCount"), s.get("weight")))
    return out


def wk(exercises):
    return StrongWorkout(id="w1", name="Test", start=START,
                         end=START + timedelta(minutes=30), exercises=exercises)


# --- 1. FILL: one set per exercise, slots == sets (the Upper B shape) ---
w = wk([
    StrongExercise(id="m1", name="Lateral Raise (Dumbbell)", sets=[StrongSet(reps=22, weight=8.5)]),
    StrongExercise(id="m2", name="Pull Up", sets=[StrongSet(reps=12, weight=0.0)]),
    StrongExercise(id="m3", name="Bench Press (Barbell)", sets=[StrongSet(reps=8, weight=60.0)]),
])
payload, summ = enrich.build_payload(watch_sets(3), w, UNIT)
assert summ["mode"] == "fill", summ
av = active_view(payload)
assert av == [
    ("LATERAL_RAISE", None, 22, 8500),
    ("PULL_UP", "PULL_UP", 12, 0),
    ("BENCH_PRESS", "BARBELL_BENCH_PRESS", 8, 60000),
], av
print("1 FILL 1:1 OK:", av)


# --- 2. FILL with a spare watch slot (slots > sets): extras left untouched ---
payload, summ = enrich.build_payload(watch_sets(4), w, UNIT)
assert summ["mode"] == "fill", summ
assert len(active_view(payload)) == 3            # only 3 slots filled
assert sum(1 for s in payload["exerciseSets"] if s["setType"] == "ACTIVE") == 4  # 4th slot kept
print("2 FILL slots>sets OK (4th ACTIVE slot untouched)")


# --- 3. REPLACE when slots < sets: the mislabel regression ---
# 3 exercises x 2 sets = 6 sets, but the watch logged only 2 ACTIVE blocks. The OLD code
# crammed flat[0:2] into the 2 slots and appended flat[2:] -> bench,bench then squat... at
# the END, scrambling order. The fix must synthesise all 6 in true per-exercise order.
w2 = wk([
    StrongExercise(id="m1", name="Bench Press (Barbell)",
                   sets=[StrongSet(reps=5, weight=60.0), StrongSet(reps=5, weight=62.5)]),
    StrongExercise(id="m2", name="Squat (Barbell)",
                   sets=[StrongSet(reps=3, weight=100.0), StrongSet(reps=3, weight=102.5)]),
    StrongExercise(id="m3", name="Pull Up",
                   sets=[StrongSet(reps=10, weight=0.0), StrongSet(reps=8, weight=0.0)]),
])
payload, summ = enrich.build_payload(watch_sets(2), w2, UNIT)
assert summ["mode"] == "replace", summ
av = active_view(payload)
assert av == [
    ("BENCH_PRESS", "BARBELL_BENCH_PRESS", 5, 60000),
    ("BENCH_PRESS", "BARBELL_BENCH_PRESS", 5, 62500),
    ("SQUAT", "BARBELL_BACK_SQUAT", 3, 100000),
    ("SQUAT", "BARBELL_BACK_SQUAT", 3, 102500),
    ("PULL_UP", "PULL_UP", 10, 0),
    ("PULL_UP", "PULL_UP", 8, 0),
], av
times = [s["startTime"] for s in payload["exerciseSets"] if s["setType"] == "ACTIVE"]
assert times == sorted(times), "synthesised sets out of chronological order"
print("3 REPLACE slots<sets OK (no scramble):")
for row in av:
    print("   ", row)


# --- 4. assisted -> weight None; use_names=False -> category only ---
w3 = wk([StrongExercise(id="m1", name="Pull Up",
                        sets=[StrongSet(reps=8, weight=20.0, assisted=True)])])
cat, name, reps, wt = active_view(enrich.build_payload(watch_sets(1), w3, UNIT)[0])[0]
assert wt is None, f"assisted weight should be None, got {wt}"
cat, name, reps, wt = active_view(
    enrich.build_payload(watch_sets(1), w3, UNIT, use_names=False)[0])[0]
assert cat == "PULL_UP" and name is None, (cat, name)
print("4 assisted->None, use_names=False->category-only OK")


# --- 5. empty workout is a no-op (must not wipe the watch's sets) ---
empty = watch_sets(2)
payload, summ = enrich.build_payload(empty, wk([]), UNIT)
assert summ["mode"] == "noop", summ
assert len(payload["exerciseSets"]) == len(empty["exerciseSets"])
print("5 empty workout no-op OK")

print("\nALL ENRICH TESTS PASSED")
