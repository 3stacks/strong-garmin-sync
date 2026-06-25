"""Tiny JSON dedup store: which Strong workouts we've already pushed to Garmin."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("sgs.state")


class State:
    def __init__(self, path: Path):
        self.path = path
        self._d: dict[str, dict] = {}
        if path.exists():
            try:
                self._d = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as e:
                log.warning("could not read state %s (%s); starting fresh", path, e)

    def processed(self, strong_id: str) -> bool:
        return strong_id in self._d

    def items(self) -> dict:
        return dict(self._d)

    def remove(self, strong_id: str) -> None:
        if self._d.pop(strong_id, None) is not None:
            self.save()

    def record(self, strong_id: str, *, garmin_activity_id, replaced_activity_id,
               uploaded_at: str, note: str = "") -> None:
        self._d[strong_id] = {
            "garmin_activity_id": garmin_activity_id,
            "replaced_activity_id": replaced_activity_id,
            "uploaded_at": uploaded_at,
            "note": note,
        }
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self._d, indent=2, sort_keys=True))
