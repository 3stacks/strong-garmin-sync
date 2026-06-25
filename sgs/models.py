"""Normalised Strong workout model (decoupled from Strong's wire format)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class StrongSet:
    """One set. `weight` is in the unit Strong sends (see Config.strong_weight_unit);
    conversion to FIT's kg happens at encode time. `duration` (s) is set for time-based
    moves (e.g. planks) that have no reps."""
    reps: int
    weight: float | None = None      # raw value in STRONG_WEIGHT_UNIT (added weight for weighted bodyweight)
    duration: float | None = None    # seconds, for DURATION sets
    rpe: float | None = None         # no native FIT field; dropped on encode
    assisted: bool = False           # ASSISTED_BODYWEIGHT set (weight is assistance, not load)
    is_completed: bool = True


@dataclass
class StrongExercise:
    id: str                          # measurement id (the exercise definition)
    name: str                        # resolved display name, e.g. "Bench Press (Barbell)"
    sets: list[StrongSet] = field(default_factory=list)


@dataclass
class StrongWorkout:
    id: str                          # Strong log id — primary dedup key
    name: str
    start: datetime                  # tz-aware
    end: datetime | None             # tz-aware
    exercises: list[StrongExercise] = field(default_factory=list)

    @property
    def duration(self) -> timedelta | None:
        return (self.end - self.start) if self.end else None

    @property
    def total_sets(self) -> int:
        return sum(len(e.sets) for e in self.exercises)

    def __str__(self) -> str:
        when = self.start.isoformat()
        return f"<StrongWorkout {self.id[:8]} {self.name!r} {when} {len(self.exercises)}ex {self.total_sets}sets>"
