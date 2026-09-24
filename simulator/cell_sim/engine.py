"""Discrete-event engine.

The engine knows nothing about wall-clock time or OPC UA: it turns a config (and optionally a
persisted state) into a deterministic, time-ordered stream of tag changes stamped with simulated
time. A pacer releases them in real time; tests just run it flat out.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable

from .cell import Cell
from .chaos import ChaosInjector
from .config import SimConfig
from .model import TAGS, UINT32_MASK, TagChange, TagType, TagValue
from .persistence import PersistedState
from .rng import RngFactory

Handler = Callable[[object, int | None], None]


class Simulation:
    def __init__(self, cfg: SimConfig, restored: PersistedState | None = None) -> None:
        self._start_ts = restored.resume_ts if restored else cfg.clock.start_ts
        self._now = self._start_ts
        self._heap: list[tuple[float, int, str, int | None, object]] = []
        self._seq = itertools.count()
        self._handlers: dict[str, Handler] = {}
        self._image: dict[str, TagValue] = {}
        self._changes: list[TagChange] = []

        # After a restart the streams are re-derived from the resume time, so a restarted run
        # does not replay the random sequence of the first day.
        salt = str(int(self._start_ts)) if restored else "0"
        self.cell = Cell(cfg, self, RngFactory(cfg.seed, salt), restored)
        self._chaos = ChaosInjector(cfg.chaos, self, self.cell)

    # ------------------------------------------------------------------ Host interface

    @property
    def now(self) -> float:
        return self._now

    def schedule(
        self, delay_s: float, kind: str, epoch: int | None = None, payload: object = None
    ) -> None:
        heapq.heappush(self._heap, (self._now + delay_s, next(self._seq), kind, epoch, payload))

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def set_tag(self, path: str, value: TagValue) -> None:
        """Write a tag; only actual changes are emitted (the first write always is)."""
        value = _coerce(TAGS[path], value)
        if path in self._image and self._image[path] == value:
            return
        self._image[path] = value
        self._changes.append(TagChange(self._now, path, value))

    # ------------------------------------------------------------------ driving

    @property
    def image(self) -> dict[str, TagValue]:
        """Current value of every tag."""
        return dict(self._image)

    @property
    def start_ts(self) -> float:
        return self._start_ts

    @property
    def next_event_time(self) -> float:
        return self._heap[0][0]

    def start(self) -> list[TagChange]:
        """Publish the initial image of every tag, stamped with the start time."""
        self._chaos.apply_at_start()
        self.cell.start()
        self._chaos.arm()
        return self._drain()

    def step(self) -> list[TagChange]:
        """Process the next event and return the tag changes it caused."""
        ts, _seq, kind, epoch, payload = heapq.heappop(self._heap)
        self._now = ts
        self._handlers[kind](payload, epoch)
        return self._drain()

    def run_until(self, end_ts: float) -> list[TagChange]:
        out: list[TagChange] = []
        while self.next_event_time <= end_ts:
            out.extend(self.step())
        return out

    def force_fault(self, code: int, duration_s: float, at: float) -> list[TagChange] | None:
        """Start a fault at simulated time ``at``; ``None`` if it cannot be done right now.

        Refused while events are still due at or before ``at`` (time must never go backwards)
        and when the cell is not producing."""
        if self.next_event_time <= at:
            return None
        self._now = max(self._now, at)
        if not self.cell.force_fault(code, duration_s):
            return None
        return self._drain()

    def snapshot(
        self, *, clean: bool, now: float | None = None, lookahead_s: float = 0.0
    ) -> PersistedState:
        """Retentive data. ``now`` is the pacer's clock; the state never claims to be older than
        what was published. For an unclean save, ``lookahead_s`` of simulated time is added to
        the resume point so a crash cannot make time go backwards."""
        t = max(self._now, now if now is not None else self._now)
        return self.cell.snapshot(t, t if clean else t + lookahead_s, clean)

    def _drain(self) -> list[TagChange]:
        out, self._changes = self._changes, []
        return out


def _coerce(tag_type: TagType, value: TagValue) -> TagValue:
    match tag_type:
        case TagType.INT16:
            return int(value)
        case TagType.UINT32:
            return int(value) & UINT32_MASK
        case TagType.FLOAT:
            return float(value)
        case TagType.BOOLEAN:
            return bool(value)
