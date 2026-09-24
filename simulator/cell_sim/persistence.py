"""Retentive data: what a PLC keeps in a non-volatile DB across a restart."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .wear import WearState

log = logging.getLogger(__name__)

STATE_VERSION = 1


class PersistedState(BaseModel):
    version: int = STATE_VERSION
    sim_time: float  # POSIX seconds; latest simulated time published
    # Where to resume after an *unclean* stop: ahead of anything that may have been published
    # since the last save, so simulated time never goes backwards (the downtime shows up as a gap).
    high_water: float
    clean_shutdown: bool
    good_count: int
    scrap_count: int
    heartbeat: int
    wear: WearState
    next_dressing_parts: int
    next_cap_change_parts: int

    @property
    def resume_ts(self) -> float:
        return self.sim_time if self.clean_shutdown else self.high_water


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> PersistedState | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            state = PersistedState.model_validate_json(raw)
        except ValidationError:
            log.warning("ignoring unreadable state file %s", self._path)
            return None
        if state.version != STATE_VERSION:
            log.warning("ignoring state file %s: unsupported version %s", self._path, state.version)
            return None
        return state

    def save(self, state: PersistedState) -> None:
        """Atomic: a crash mid-write leaves the previous file intact."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(state.model_dump_json())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)
