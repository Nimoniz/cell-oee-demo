"""The cell state machine.

One non-producing state is active at a time. Time-driven callbacks carry the *epoch* they were
scheduled in; every state change bumps the epoch, which silently invalidates stale callbacks.

Rules worth knowing:

* Breakdowns, starved and blocked periods only arise while PRODUCING.
* A planned break is fixed in time. It starts on schedule (at the end of the current part if the
  cell is producing) and ends on schedule. Starved/blocked periods are pre-empted by it; a fault,
  a setup or a manual recovery in progress carries on and the break is shortened accordingly.
* Tip dressing and cap change are due at part boundaries and *are* deferred by a break.
* Fault 201 follows N consecutive parts with a weld point out of tolerance, and forces a tip
  dressing when production resumes.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal, Protocol

from .config import SimConfig
from .faults import DisturbanceModel, Interruption
from .model import (
    FAULTS,
    STATIONS,
    UINT32_MASK,
    WELD_FAULT_CODE,
    CellState,
    Mode,
    StopOrigin,
    TagValue,
    default_origin,
    fault_origin,
)
from .persistence import PersistedState
from .rng import RngFactory
from .shifts import BreakWindow, ShiftCalendar
from .wear import ElectrodeWear

SetupKind = Literal["dressing", "cap_change"]


class Host(Protocol):
    @property
    def now(self) -> float: ...

    def schedule(
        self, delay_s: float, kind: str, epoch: int | None = None, payload: object = None
    ) -> None: ...

    def register(self, kind: str, handler: Callable[[object, int | None], None]) -> None: ...

    def set_tag(self, path: str, value: TagValue) -> None: ...


class Cell:
    def __init__(
        self,
        cfg: SimConfig,
        host: Host,
        rng: RngFactory,
        restored: PersistedState | None = None,
    ) -> None:
        self._cfg = cfg
        self._host = host
        self._calendar = ShiftCalendar(cfg.shifts, cfg.planned_break)
        self._dist = DisturbanceModel(cfg, rng)
        self._cycle_rng = rng.stream("cycle")
        self._quality_rng = rng.stream("quality")
        self._jitter_rng = rng.stream("jitter")
        self._wear = ElectrodeWear(
            cfg.weld, rng.stream("weld"), restored.wear if restored else None
        )

        self.good = restored.good_count if restored else 0
        self.scrap = restored.scrap_count if restored else 0
        self.heartbeat = restored.heartbeat if restored else 0
        self._hb_frozen = False

        weld = cfg.weld
        self._next_dressing = (
            restored.next_dressing_parts
            if restored
            else self._threshold(weld.tip_dressing.every_parts, weld.tip_dressing.jitter_pct)
        )
        self._next_cap_change = (
            restored.next_cap_change_parts
            if restored
            else self._threshold(weld.cap_change.every_parts, weld.cap_change.jitter_pct)
        )
        self._dressing_due = False
        self._cap_change_due = False
        self._consecutive_weld_nok = 0

        self._epoch = 0
        self._state = CellState.PRODUCING
        self._break_window: BreakWindow | None = None
        self._break_active = False

        # Part in progress: ``points_per_part`` slots of equal length, one weld point per slot.
        self._part_active = False
        self._part_weld_ok = True
        self._cycle_s = cfg.cell.theoretical_cycle_s
        self._slot_idx = 0
        self._slot_len = 0.0
        self._slot_remaining = 0.0
        self._slot_end_ts: float | None = None

        for kind, handler in (
            ("slot_end", self._on_slot_end),
            ("interrupt", self._on_interrupt),
            ("stop_end", self._on_stop_end),
            ("break_start", self._on_break_start),
            ("break_end", self._on_break_end),
            ("heartbeat", self._on_heartbeat),
        ):
            host.register(kind, handler)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        cfg = self._cfg
        tag = self._host.set_tag
        tag("Cell/TheoreticalCycleTime", cfg.cell.theoretical_cycle_s)
        tag("Cell/LastCycleTime", 0.0)
        tag("Cell/GoodCount", self.good)
        tag("Cell/ScrapCount", self.scrap)
        tag("Cell/Heartbeat", self.heartbeat)
        tag("OP20/Weld/LastPointCurrent", 0.0)
        tag("OP20/Weld/PointsSinceCapChange", self._wear.state.points_since_cap_change)
        tag("OP20/Weld/LastPointOK", True)
        self._host.schedule(1.0, "heartbeat")
        self._arm_break()
        self._resume()

    def snapshot(self, sim_time: float, high_water: float, clean: bool) -> PersistedState:
        return PersistedState(
            sim_time=sim_time,
            high_water=high_water,
            clean_shutdown=clean,
            good_count=self.good,
            scrap_count=self.scrap,
            heartbeat=self.heartbeat,
            wear=self._wear.state.model_copy(),
            next_dressing_parts=self._next_dressing,
            next_cap_change_parts=self._next_cap_change,
        )

    # ------------------------------------------------------------------ chaos hooks

    def set_counters(self, good: int, scrap: int) -> None:
        self.good, self.scrap = good & UINT32_MASK, scrap & UINT32_MASK
        self._host.set_tag("Cell/GoodCount", self.good)
        self._host.set_tag("Cell/ScrapCount", self.scrap)

    def reset_plc(self) -> None:
        """Counters and heartbeat back to zero, as after a PLC restart without retentive data."""
        self.set_counters(0, 0)
        self.heartbeat = 0
        self._host.set_tag("Cell/Heartbeat", 0)

    def freeze_heartbeat(self, frozen: bool) -> None:
        self._hb_frozen = frozen

    def force_fault(self, code: int, duration_s: float) -> bool:
        """Recording aid (see pacer): start a fault now. Only from PRODUCING, like a real one."""
        if self._state is not CellState.PRODUCING or code not in FAULTS:
            return False
        self._enter_fault(code, duration_s)
        return True

    # ------------------------------------------------------------------ helpers

    def _threshold(self, every_parts: int, jitter_pct: float) -> int:
        factor = 1 + self._jitter_rng.uniform(-jitter_pct, jitter_pct) / 100
        return max(1, round(every_parts * factor))

    def _sample_cycle_s(self) -> float:
        noise = self._cfg.cell.cycle_noise
        mu = math.log(noise.mean_extra_s) - noise.sigma**2 / 2  # so that E[extra] = mean_extra_s
        return self._cfg.cell.theoretical_cycle_s + self._cycle_rng.lognormvariate(mu, noise.sigma)

    def _enter(
        self,
        state: CellState,
        fault_code: int = 0,
        mode: Mode = Mode.AUTO,
        origin: StopOrigin | None = None,
    ) -> None:
        if self._slot_end_ts is not None:  # leaving PRODUCING mid-slot: freeze the slot
            self._slot_remaining = max(0.0, self._slot_end_ts - self._host.now)
            self._slot_end_ts = None
        self._epoch += 1
        self._state = state
        tag = self._host.set_tag
        tag("Cell/State", int(state))
        tag("Cell/Mode", int(mode))
        tag("Cell/FaultCode", fault_code)
        tag(
            "Cell/StopOrigin",
            int(origin if origin is not None else default_origin(state, fault_code)),
        )
        # Stations follow the cell state; the faulty station carries the fault code.
        origin = FAULTS[fault_code].station if fault_code in FAULTS else None
        for station in STATIONS:
            tag(f"{station}/State", int(state))
            tag(f"{station}/FaultCode", fault_code if station == origin else 0)

    def _stop_for(self, duration_s: float, kind: str, arg: object = None) -> None:
        self._host.schedule(duration_s, "stop_end", self._epoch, (kind, arg))

    # ------------------------------------------------------------------ transitions

    def _resume(self) -> None:
        """The cell is free: pick what comes next. Break > cap change > dressing > production."""
        if self._break_active:
            self._enter(CellState.PLANNED_STOP)  # ends on schedule, see _on_break_end
        elif self._cap_change_due:
            self._start_setup("cap_change", self._cfg.weld.cap_change.duration_s)
        elif self._dressing_due:
            self._start_setup("dressing", self._cfg.weld.tip_dressing.duration_s)
        else:
            self._start_producing()

    def _start_setup(self, kind: SetupKind, duration_s: float) -> None:
        self._enter(CellState.SETUP)
        self._dressing_due = False
        if kind == "cap_change":
            self._cap_change_due = False
        self._stop_for(duration_s, "setup", kind)

    def _start_producing(self) -> None:
        self._enter(CellState.PRODUCING)
        if not self._part_active:
            self._cycle_s = self._sample_cycle_s()
            self._slot_len = self._cycle_s / self._cfg.weld.points_per_part
            self._slot_idx = 0
            self._slot_remaining = self._slot_len
            self._part_weld_ok = True
            self._part_active = True
        self._schedule_slot()
        it = self._dist.next_interruption()
        if it is not None:
            self._host.schedule(it.delay_s, "interrupt", self._epoch, it)

    def _schedule_slot(self) -> None:
        self._slot_end_ts = self._host.now + self._slot_remaining
        self._host.schedule(self._slot_remaining, "slot_end", self._epoch)

    def _enter_fault(self, code: int, duration_s: float | None = None) -> None:
        self._enter(CellState.FAULT, fault_code=code)
        self._stop_for(duration_s or self._dist.fault_duration_s(code), "fault", code)

    # ------------------------------------------------------------------ handlers

    def _on_slot_end(self, _payload: object, epoch: int | None) -> None:
        if epoch != self._epoch:
            return
        self._slot_end_ts = None
        point = self._wear.weld_point()
        tag = self._host.set_tag
        tag("OP20/Weld/LastPointCurrent", round(point.current_ka, 3))
        tag("OP20/Weld/LastPointOK", point.ok)
        tag("OP20/Weld/PointsSinceCapChange", self._wear.state.points_since_cap_change)
        self._part_weld_ok &= point.ok
        self._slot_idx += 1
        if self._slot_idx < self._cfg.weld.points_per_part:
            self._slot_remaining = self._slot_len
            self._schedule_slot()
        else:
            self._complete_part()

    def _complete_part(self) -> None:
        self._part_active = False
        weld_nok = not self._part_weld_ok
        scrap = weld_nok or self._quality_rng.random() < self._cfg.cell.base_scrap_rate
        if scrap:
            self.scrap = (self.scrap + 1) & UINT32_MASK
            self._host.set_tag("Cell/ScrapCount", self.scrap)
        else:
            self.good = (self.good + 1) & UINT32_MASK
            self._host.set_tag("Cell/GoodCount", self.good)
        self._host.set_tag("Cell/LastCycleTime", round(self._cycle_s, 3))
        self._wear.part_done()

        self._consecutive_weld_nok = self._consecutive_weld_nok + 1 if weld_nok else 0
        if self._consecutive_weld_nok >= self._cfg.weld.fault_201.consecutive_nok_parts:
            self._consecutive_weld_nok = 0
            self._dressing_due = True  # forced dressing once the fault is cleared
            self._enter_fault(WELD_FAULT_CODE)
            return

        state = self._wear.state
        if state.parts_since_cap_change >= self._next_cap_change:
            self._cap_change_due = True
        elif state.parts_since_dressing >= self._next_dressing:
            self._dressing_due = True
        self._resume()

    def _on_interrupt(self, payload: object, epoch: int | None) -> None:
        if epoch != self._epoch:
            return
        assert isinstance(payload, Interruption)
        if payload.kind == "fault":
            self._enter_fault(payload.code)
        elif payload.kind == "starved":
            self._enter(CellState.STARVED)
            self._stop_for(self._dist.flow_duration_s("starved"), "starved")
        else:
            self._enter(CellState.BLOCKED)
            self._stop_for(self._dist.flow_duration_s("blocked"), "blocked")

    def _on_stop_end(self, payload: object, epoch: int | None) -> None:
        if epoch != self._epoch:
            return
        assert isinstance(payload, tuple)
        kind, arg = payload
        if kind == "fault" and isinstance(arg, int) and self._dist.manual_follows(arg):
            # Manual recovery happens where the fault was.
            self._enter(CellState.MANUAL, mode=Mode.MANUAL, origin=fault_origin(arg))
            self._stop_for(self._dist.manual_duration_s(), "manual")
            return
        if kind == "setup":
            self._finish_setup(arg)
        self._resume()

    def _finish_setup(self, kind: object) -> None:
        weld = self._cfg.weld
        if kind == "cap_change":
            self._wear.change_caps()
            self._next_cap_change = self._threshold(
                weld.cap_change.every_parts, weld.cap_change.jitter_pct
            )
            self._host.set_tag("OP20/Weld/PointsSinceCapChange", 0)
        else:
            self._wear.dress()
        self._next_dressing = self._threshold(
            weld.tip_dressing.every_parts, weld.tip_dressing.jitter_pct
        )

    # ------------------------------------------------------------------ planned break

    def _arm_break(self, after: float | None = None) -> None:
        now = self._host.now
        window = self._calendar.next_window(now if after is None else after)
        self._break_window = window
        if window.start <= now:  # started before now (restart inside a break)
            self._break_active = True
            self._host.schedule(window.end - now, "break_end")
        else:
            self._host.schedule(window.start - now, "break_start")

    def _on_break_start(self, _payload: object, _epoch: int | None) -> None:
        assert self._break_window is not None
        self._break_active = True
        self._host.schedule(self._break_window.end - self._host.now, "break_end")
        if self._state in (CellState.STARVED, CellState.BLOCKED):
            self._resume()  # idle line: the break takes over immediately
        # PRODUCING: the break starts at the end of the part in progress (see _complete_part).
        # FAULT / SETUP / MANUAL: they carry on; the break resumes once they are done.

    def _on_break_end(self, _payload: object, _epoch: int | None) -> None:
        assert self._break_window is not None
        self._break_active = False
        # Look past this window's end: float rounding must not make it come back as "next".
        self._arm_break(after=max(self._host.now, self._break_window.end) + 1.0)
        if self._state is CellState.PLANNED_STOP:
            self._resume()

    # ------------------------------------------------------------------ heartbeat

    def _on_heartbeat(self, _payload: object, _epoch: int | None) -> None:
        if not self._hb_frozen:
            self.heartbeat = (self.heartbeat + 1) & UINT32_MASK
            self._host.set_tag("Cell/Heartbeat", self.heartbeat)
        self._host.schedule(1.0, "heartbeat")
