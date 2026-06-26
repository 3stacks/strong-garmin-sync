"""Build a Garmin `exerciseSets` PUT payload from a Strong workout, enriching a watch
activity's sets (HR/time-series untouched).

Two modes, chosen by how the watch's ACTIVE slots line up with Strong's sets:
  * FILL    — at least one usable ACTIVE slot per Strong set: drop each Strong set into
              its own slot in chronological order (1:1), preserving the watch's real
              per-set timing. Any extra trailing ACTIVE slots and all REST slots are
              left untouched.
  * REPLACE — fewer usable ACTIVE slots than Strong sets (the watch grouped several sets
              into one block, or logged one long block): positional filling would mislabel
              sets, so drop the watch's structure and synthesise one ACTIVE set per Strong
              set, in order, spread across the session span.
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dateutil import parser as dtparser

from . import exercise_map
from .models import StrongWorkout

log = logging.getLogger("sgs.enrich")

LB_TO_KG = 0.45359237


def to_grams(weight, unit: str) -> int | None:
    if weight is None:
        return None
    kg = float(weight) * (LB_TO_KG if unit == "lb" else 1.0)
    return round(kg * 1000)


def _flatten(workout: StrongWorkout):
    """[(strong_name, category, name, StrongSet), ...] in workout order."""
    flat = []
    for ex in workout.exercises:
        cat, name = exercise_map.lookup(ex.name)
        for s in ex.sets:
            flat.append((ex.name, cat, name, s))
    return flat


def _parse_t(s):
    try:
        return dtparser.isoparse(s) if s else None
    except (ValueError, TypeError):
        return None


def _fmt_t(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0")


def _session_span(sets):
    starts, ends = [], []
    for s in sets:
        t = _parse_t(s.get("startTime"))
        if t:
            starts.append(t)
            ends.append(t + timedelta(seconds=float(s.get("duration") or 0)))
    return (min(starts), max(ends)) if starts else (None, None)


def _make_set(cat, name, sset, unit, use_names, start_dt, duration_s, msg_idx) -> dict:
    return {
        "exercises": [{"category": cat, "name": name if use_names else None}],
        "repetitionCount": int(sset.reps) if sset.reps else None,
        "weight": None if sset.assisted else to_grams(sset.weight, unit),
        "setType": "ACTIVE",
        "startTime": _fmt_t(start_dt) if start_dt else None,
        "duration": float(duration_s),
        "wktStepIndex": None,
        "messageIndex": msg_idx,
    }


def build_payload(existing: dict, workout: StrongWorkout, weight_unit: str,
                  use_names: bool = True, max_set_duration: float = 600.0):
    """Return (new_exerciseSets_dict, summary). Pure — no network."""
    es = copy.deepcopy(existing)
    sets = es.get("exerciseSets") or []
    flat = _flatten(workout)
    active_idx = [i for i, s in enumerate(sets)
                  if s.get("setType") == "ACTIVE" and (s.get("duration") or 0) <= max_set_duration]
    span_start, span_end = _session_span(sets)

    if not flat:
        # empty workout -> nothing to write; leave the activity's sets untouched
        es["exerciseSets"] = sets
        return es, {"mode": "noop", "active_slots": len(active_idx),
                    "strong_sets": 0, "appended": 0}

    if len(active_idx) >= len(flat):
        # FILL: one watch slot per Strong set, in chronological order. With at least as
        # many usable slots as sets, every set lands in its own slot, so there is no
        # overflow and no mislabelling. Extra trailing slots keep their original content.
        for si, i in enumerate(active_idx[:len(flat)]):
            _, cat, name, sset = flat[si]
            sets[i]["exercises"] = [{"category": cat, "name": name if use_names else None}]
            sets[i]["repetitionCount"] = int(sset.reps) if sset.reps else None
            sets[i]["weight"] = None if sset.assisted else to_grams(sset.weight, weight_unit)
        mode, appended = "fill", 0
    else:
        # REPLACE: fewer usable slots than sets (the watch grouped sets into blocks).
        # Positional filling would scramble exercise order, so synthesise one ACTIVE set
        # per Strong set, in order, spread across the session span.
        if not span_start:
            span_start = workout.start
            span_end = workout.end or (workout.start + timedelta(minutes=max(1, len(flat))))
        total = max(1.0, (span_end - span_start).total_seconds())
        gap = total / len(flat)
        new = []
        for k, (_, cat, name, sset) in enumerate(flat):
            st = span_start + timedelta(seconds=gap * k)
            dur = float(sset.duration) if sset.duration else max(5.0, min(gap * 0.8, 120.0))
            new.append(_make_set(cat, name, sset, weight_unit, use_names, st, dur, k))
        sets = new
        mode, appended = "replace", len(new)

    es["exerciseSets"] = sets
    summary = {"mode": mode, "active_slots": len(active_idx),
               "strong_sets": len(flat), "appended": appended}
    return es, summary


def is_enriched(exercise_sets: dict) -> bool:
    """True if any set already carries a rep count (i.e. exercise data is present)."""
    return any(s.get("repetitionCount") for s in (exercise_sets.get("exerciseSets") or []))


def enrich_pair(api, activity_id, workout: StrongWorkout, weight_unit: str,
                backup_dir: str, state=None, use_names: bool = True) -> dict:
    """GET the activity's sets, back up the ORIGINAL once, build + PUT the enriched payload
    (falling back to category-only on a bad-name 400), and record the link in `state`."""
    es = api.get_activity_exercise_sets(int(activity_id))
    backup = Path(backup_dir) / f"exercise_sets_backup_{activity_id}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_text(json.dumps(es, indent=2, default=str))

    payload, summ = build_payload(es, workout, weight_unit, use_names=use_names)
    try:
        api.set_activity_exercise_sets(int(activity_id), payload)
    except Exception:  # noqa: BLE001 - likely a bad sub-category name -> category-only
        payload, summ = build_payload(es, workout, weight_unit, use_names=False)
        api.set_activity_exercise_sets(int(activity_id), payload)
        summ["fallback"] = "category-only"

    if state is not None:
        state.record(workout.id, garmin_activity_id=str(activity_id),
                     replaced_activity_id=None,
                     uploaded_at=datetime.now(timezone.utc).isoformat(),
                     note=summ.get("mode", ""))
    return summ


def restore_activity(api, activity_id, backup_dir: str) -> bool:
    """Restore an activity's exerciseSets from its original backup. True if restored."""
    backup = Path(backup_dir) / f"exercise_sets_backup_{activity_id}.json"
    if not backup.exists():
        return False
    api.set_activity_exercise_sets(int(activity_id), json.loads(backup.read_text()))
    return True
