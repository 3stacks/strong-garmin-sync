"""Pair a Strong workout with the Garmin watch activity it corresponds to,
by start-time proximity."""
from __future__ import annotations

import logging

from .garmin_client import activity_start
from .models import StrongWorkout

log = logging.getLogger("sgs.match")


def match(workout: StrongWorkout, activities: list[dict], window_s: int) -> dict | None:
    """Return the Garmin strength activity whose start is closest to the workout's
    start within `window_s`, else None (caller decides: standalone-import fallback)."""
    best, best_delta = None, None
    for a in activities:
        astart = activity_start(a)
        if astart is None:
            continue
        delta = abs((astart - workout.start).total_seconds())
        if delta <= window_s and (best_delta is None or delta < best_delta):
            best, best_delta = a, delta
    if best is not None:
        log.info("matched %s -> Garmin %s (Δ%ds)", workout.id[:8],
                 best.get("activityId"), int(best_delta))
    else:
        log.info("no Garmin activity within %ds of %s (%s)", window_s,
                 workout.id[:8], workout.start.isoformat())
    return best
