"""Strong exercise display-name -> Garmin (category, name) STRING enums for the
exerciseSets API.

`category` is the parent enum (e.g. "BENCH_PRESS"); `name` is the per-category sub-enum
(e.g. "BARBELL_BENCH_PRESS") or None. Garmin validates these against its FIT enum and 400s
on unknown values, but accepts `name=None` under any known `category`. Enum strings derive
from fit-tool 0.9.15's profile (verified by the FIT-research step).

v1 reliability: callers may send category + name=None (category alone is validated to render
correctly, e.g. "BENCH_PRESS"). The specific `name` is kept here to enable once the accepted
sub-category string set is validated against the live API.
"""
from __future__ import annotations

import logging

log = logging.getLogger("sgs.exmap")

DEFAULT: tuple[str, str | None] = ("UNKNOWN", None)

# Keyed by exact Strong display name -> (category, name|None).
STRONG_TO_FIT: dict[str, tuple[str, str | None]] = {
    # chest / bench
    "Bench Press (Barbell)": ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "Incline Bench Press (Barbell)": ("BENCH_PRESS", "INCLINE_BARBELL_BENCH_PRESS"),
    "Dumbbell Bench Press": ("BENCH_PRESS", "DUMBBELL_BENCH_PRESS"),
    "Incline Bench Press (Dumbbell)": ("BENCH_PRESS", "INCLINE_DUMBBELL_BENCH_PRESS"),
    # squat / legs
    "Squat (Barbell)": ("SQUAT", "BARBELL_BACK_SQUAT"),
    "Front Squat (Barbell)": ("SQUAT", "BARBELL_FRONT_SQUAT"),
    "Goblet Squat (Dumbbell)": ("SQUAT", "GOBLET_SQUAT"),
    "Leg Press": ("SQUAT", "LEG_PRESS"),
    "Overhead Squat (Barbell)": ("SQUAT", "OVERHEAD_SQUAT"),
    # deadlift
    "Deadlift (Barbell)": ("DEADLIFT", "BARBELL_DEADLIFT"),
    "Romanian Deadlift (Barbell)": ("DEADLIFT", "BARBELL_STRAIGHT_LEG_DEADLIFT"),
    "Sumo Deadlift (Barbell)": ("DEADLIFT", "SUMO_DEADLIFT"),
    # shoulder press
    "Overhead Press (Barbell)": ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "Overhead Press (Dumbbell)": ("SHOULDER_PRESS", "OVERHEAD_DUMBBELL_PRESS"),
    "Seated Overhead Press (Dumbbell)": ("SHOULDER_PRESS", "SEATED_DUMBBELL_SHOULDER_PRESS"),
    "Arnold Press (Dumbbell)": ("SHOULDER_PRESS", "ARNOLD_PRESS"),
    # rows (no bilateral barbell-row sub-enum -> category-only)
    "Bent Over Row (Barbell)": ("ROW", None),
    "Pendlay Row (Barbell)": ("ROW", None),
    "Seated Row (Cable)": ("ROW", "SEATED_CABLE_ROW"),
    "Dumbbell Row": ("ROW", "DUMBBELL_ROW"),
    "Face Pull (Cable)": ("ROW", "FACE_PULL"),
    "T Bar Row (Barbell)": ("ROW", "T_BAR_ROW"),
    # pulls
    "Pull Up": ("PULL_UP", "PULL_UP"),
    "Chin Up": ("PULL_UP", "CLOSE_GRIP_CHIN_UP"),
    "Lat Pulldown (Cable)": ("PULL_UP", "LAT_PULLDOWN"),
    # curls
    "Bicep Curl (Barbell)": ("CURL", "BARBELL_BICEPS_CURL"),
    "Bicep Curl (Dumbbell)": ("CURL", "STANDING_DUMBBELL_BICEPS_CURL"),
    "Hammer Curl (Dumbbell)": ("CURL", "DUMBBELL_HAMMER_CURL"),
    "Preacher Curl (Barbell)": ("CURL", "EZ_BAR_PREACHER_CURL"),
    # triceps
    "Triceps Pushdown (Cable)": ("TRICEPS_EXTENSION", "TRICEPS_PRESSDOWN"),
    "Triceps Extension": ("TRICEPS_EXTENSION", None),
    "Skullcrusher (Barbell)": ("TRICEPS_EXTENSION", None),
    # shoulders iso
    "Lateral Raise (Dumbbell)": ("LATERAL_RAISE", None),
    # legs iso
    "Leg Extension (Machine)": ("UNKNOWN", None),       # no leg-extension category in FIT
    "Lying Leg Curl (Machine)": ("LEG_CURL", "LEG_CURL"),
    "Seated Leg Curl (Machine)": ("LEG_CURL", "LEG_CURL"),
    # glutes / hips
    "Hip Thrust (Barbell)": ("HIP_RAISE", "BARBELL_HIP_THRUST_WITH_BENCH"),
    # calves
    "Standing Calf Raise (Dumbbell)": ("CALF_RAISE", "STANDING_CALF_RAISE"),
    "Standing Calf Raise (Machine)": ("CALF_RAISE", "STANDING_CALF_RAISE"),
    "Seated Calf Raise (Machine)": ("CALF_RAISE", "SEATED_CALF_RAISE"),
    "Calf Press (Machine)": ("CALF_RAISE", "STANDING_CALF_RAISE"),
    # back / posterior
    "Back Extension": ("HYPEREXTENSION", None),
    # core
    "Plank": ("PLANK", "PLANK"),
    # --- more exercises (categories + names validated against the live API) ---
    "Bench Press (Dumbbell)": ("BENCH_PRESS", "DUMBBELL_BENCH_PRESS"),
    "Snatch Grip High Pull": ("OLYMPIC_LIFT", None),
    "Bulgarian Split Squat": ("LUNGE", None),
    "Decline Crunch": ("CRUNCH", None),
    "Chest Fly (Dumbbell)": ("FLYE", None),
    "Seated French Press": ("TRICEPS_EXTENSION", None),
    "Slantboard Squat": ("SQUAT", None),
    "Banded Leg Curl": ("LEG_CURL", "LEG_CURL"),
    "Hanging Leg Raise": ("LEG_RAISE", None),
    "Pullover (Dumbbell)": ("UNKNOWN", None),   # no suitable FIT category
    # --- Upper B gaps (added 2026-06-26, sub-names validated against the live API) ---
    "Swiss Bar OHP": ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),   # swiss/football bar = neutral-grip barbell OHP
    "Reverse Fly (Dumbbell)": ("LATERAL_RAISE", "BENT_OVER_LATERAL_RAISE"),  # rear-delt fly = bent-over lateral raise
    "Chest Dip": ("TRICEPS_EXTENSION", "BODY_WEIGHT_DIP"),   # FIT files all dips under TRICEPS_EXTENSION
    "Incline Row (Dumbbell)": ("ROW", "DUMBBELL_ROW"),
}

_LOWER = {k.lower(): v for k, v in STRONG_TO_FIT.items()}
_warned: set[str] = set()


def lookup(strong_name: str) -> tuple[str, str | None]:
    if strong_name in STRONG_TO_FIT:
        return STRONG_TO_FIT[strong_name]
    v = _LOWER.get(strong_name.strip().lower())
    if v is not None:
        return v
    if strong_name not in _warned:
        _warned.add(strong_name)
        log.info("no exercise mapping for %r -> UNKNOWN category (reps/weight still render)",
                 strong_name)
    return DEFAULT
