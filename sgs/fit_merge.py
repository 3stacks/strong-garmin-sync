"""Merge the watch activity's HR/time-series with Strong's set data into one .fit.

Design (see README "merge path"):
  - Keep the watch FIT's `record` (HR) stream + lap/session/activity envelope intact.
  - Drop the watch's auto-detected `set` messages; write fresh ones from Strong with the
    correct exercise (category/subtype), weight (kg), and reps/duration.
  - Strong has no per-set wall-clock, so map Strong sets positionally onto the watch's
    detected set time-slots; overflow gets synthetic times within the session span.

FIT specifics (verified against fit-tool 0.9.15, FIT Profile 21.60):
  - timestamps are ms since the Unix epoch; weight is plain kg (lib applies the ×16 scale);
    `category`/`category_subtype` are arrays; enums are set via `.value`.
  - `FitFileBuilder(auto_define=True)` is required when injecting brand-new SetMessages.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fit_tool.fit_file import FitFile
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.set_message import SetMessage
from fit_tool.profile import profile_type as _profile_type
from fit_tool.profile.profile_type import (
    ExerciseCategory, FileType, Manufacturer, SetType, Sport, SubSport,
)

from . import exercise_map
from .config import Config
from .models import StrongWorkout

log = logging.getLogger("sgs.merge")

LB_TO_KG = 0.45359237


# ---- neutral intermediate (independent of the FIT lib) --------------------
@dataclass
class HrSample:
    timestamp: datetime
    heart_rate: int | None


@dataclass
class TimeSlot:
    start_time: datetime
    duration_s: float


@dataclass
class WatchFit:
    records: list[HrSample] = field(default_factory=list)
    set_slots: list[TimeSlot] = field(default_factory=list)
    session_start: datetime | None = None
    session_end: datetime | None = None
    raw: FitFile | None = None     # so write can re-emit non-`set` messages verbatim


@dataclass
class OutSet:
    start_time: datetime
    duration_s: float
    reps: int
    weight_kg: float | None
    category: int
    subtype: int | None


def to_kg(weight, unit: str) -> float | None:
    if weight is None:
        return None
    return float(weight) * (LB_TO_KG if unit == "lb" else 1.0)


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round(dt.timestamp()) * 1000


def _from_ms(ms) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ---- exercise enum translation --------------------------------------------
_UNKNOWN_CATEGORY = ExerciseCategory.UNKNOWN.value


def _name_enum_for(category: str):
    """The per-category exercise-name enum class for a FIT category string, by the FIT
    profile's naming convention (e.g. 'BENCH_PRESS' -> BenchPressExerciseName). None if the
    category has no sub-name enum."""
    cls = "".join(p.capitalize() for p in category.split("_")) + "ExerciseName"
    return getattr(_profile_type, cls, None)


def _fit_enums(strong_name: str) -> tuple[int, int | None]:
    """Resolve a Strong exercise name to FIT integer (category, category_subtype) enums.

    exercise_map yields the FIT *string* enum names (what the exerciseSets JSON API wants);
    the binary FIT format needs their integer values, so translate here. An unknown category
    or sub-name degrades to UNKNOWN / category-only rather than raising."""
    cat_s, name_s = exercise_map.lookup(strong_name)
    try:
        cat = ExerciseCategory[cat_s].value
    except KeyError:
        log.warning("no FIT category %r for %r -> UNKNOWN", cat_s, strong_name)
        return _UNKNOWN_CATEGORY, None
    if not name_s:
        return cat, None
    enum_cls = _name_enum_for(cat_s)
    if enum_cls is not None and name_s in enum_cls.__members__:
        return cat, enum_cls[name_s].value
    log.warning("no FIT sub-name %r under %s for %r -> category only",
                name_s, cat_s, strong_name)
    return cat, None


# ---- pure planning --------------------------------------------------------
def plan_sets(workout: StrongWorkout, watch: WatchFit | None, cfg: Config) -> list[OutSet]:
    flat = [(ex, s) for ex in workout.exercises for s in ex.sets]
    slots = list(watch.set_slots) if watch else []

    span_start = (watch.session_start if watch and watch.session_start else workout.start)
    span_end = (watch.session_end if watch and watch.session_end else workout.end) or (
        span_start + timedelta(minutes=max(1, len(flat))))
    overflow = max(0, len(flat) - len(slots))
    step = ((span_end - span_start).total_seconds() / overflow) if overflow else 0.0

    out: list[OutSet] = []
    oi = 0
    for i, (ex, s) in enumerate(flat):
        cat, sub = _fit_enums(ex.name)
        weight_kg = None if s.assisted else to_kg(s.weight, cfg.strong_weight_unit)
        if i < len(slots):
            st, slot_dur = slots[i].start_time, slots[i].duration_s
        else:
            st = span_start + timedelta(seconds=step * oi)
            slot_dur = 30.0
            oi += 1
        duration = float(s.duration) if s.duration else slot_dur   # time-based sets keep their own duration
        out.append(OutSet(start_time=st, duration_s=duration, reps=s.reps,
                          weight_kg=weight_kg, category=cat, subtype=sub))

    if watch and len(slots) != len(flat):
        log.warning("set-count mismatch for %s: watch detected %d, Strong has %d "
                    "(positional map; %d synthesised)",
                    workout.id[:8], len(slots), len(flat), overflow)
    return out


def build_merged_fit(workout: StrongWorkout, watch_fit_bytes: bytes | None,
                     cfg: Config) -> bytes:
    """Parse watch fit (if any), plan sets, write merged fit bytes. With no watch match,
    produces a standalone strength activity (no HR)."""
    watch = read_fit(watch_fit_bytes) if watch_fit_bytes else None
    out_sets = plan_sets(workout, watch, cfg)
    return write_merged(watch, out_sets, workout)


# ---- FIT decode -----------------------------------------------------------
def read_fit(data: bytes) -> WatchFit:
    ff = FitFile.from_bytes(bytes(data))
    records: list[HrSample] = []
    slots: list[TimeSlot] = []
    sess_start = sess_end = None
    for rec in ff.records:
        m = rec.message
        if isinstance(m, RecordMessage):
            if m.timestamp is not None:
                records.append(HrSample(_from_ms(m.timestamp), m.heart_rate))
        elif isinstance(m, SetMessage):
            st = m.start_time if m.start_time is not None else m.timestamp
            if st is not None:
                slots.append(TimeSlot(_from_ms(st), float(m.duration or 0.0)))
        elif isinstance(m, SessionMessage):
            if m.start_time is not None:
                sess_start = _from_ms(m.start_time)
                if m.total_elapsed_time:
                    sess_end = sess_start + timedelta(seconds=float(m.total_elapsed_time))
    slots.sort(key=lambda t: t.start_time)
    log.info("watch fit: %d HR records, %d detected set-slots", len(records), len(slots))
    return WatchFit(records=records, set_slots=slots,
                    session_start=sess_start, session_end=sess_end, raw=ff)


# ---- FIT encode -----------------------------------------------------------
def _build_set_message(idx: int, o: OutSet) -> SetMessage:
    s = SetMessage()
    s.message_index = idx
    s.set_type = SetType.ACTIVE.value
    s.start_time = _ms(o.start_time)
    s.timestamp = _ms(o.start_time + timedelta(seconds=o.duration_s))
    s.duration = float(o.duration_s)
    s.repetitions = int(o.reps)
    if o.weight_kg is not None:
        s.weight = float(o.weight_kg)
    s.category = [o.category]
    if o.subtype is not None:
        s.category_subtype = [o.subtype]
    return s


def write_merged(watch: WatchFit | None, out_sets: list[OutSet],
                 workout: StrongWorkout) -> bytes:
    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    if watch is not None and watch.raw is not None:
        # keep file_id, records (HR), lap, session, activity, device_info, ... ; drop watch sets
        for rec in watch.raw.records:
            if isinstance(rec.message, SetMessage):
                continue
            builder.add(rec.message)
    else:
        # standalone scaffold (no HR): file_id only; session/activity appended below
        fid = FileIdMessage()
        fid.type = FileType.ACTIVITY
        fid.manufacturer = Manufacturer.GARMIN.value
        fid.product = 0
        fid.time_created = _ms(workout.start)
        fid.serial_number = 0x53475331  # "SGS1"
        builder.add(fid)

    builder.add_all([_build_set_message(i, o) for i, o in enumerate(out_sets)])

    if watch is None:
        start = workout.start
        end = workout.end or (out_sets[-1].start_time if out_sets else start)
        sess = SessionMessage()
        sess.start_time = _ms(start)
        sess.timestamp = _ms(end)
        sess.total_elapsed_time = max(0.0, (end - start).total_seconds())
        sess.total_timer_time = sess.total_elapsed_time
        sess.sport = Sport.TRAINING.value
        sess.sub_sport = SubSport.STRENGTH_TRAINING.value
        sess.first_lap_index = 0
        sess.num_laps = 1
        builder.add(sess)
        act = ActivityMessage()
        act.timestamp = _ms(end)
        act.num_sessions = 1
        builder.add(act)

    return bytes(builder.build().to_bytes())
