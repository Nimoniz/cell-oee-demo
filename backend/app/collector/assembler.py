"""Groups notifications by SourceTimestamp and releases them in timestamp order.

Notifications for one instant do not necessarily arrive together (separate monitored items,
separate publish responses), and the values sent when a subscription is (re)created carry
old timestamps in any order. Arrival time is never used for anything but *waiting*: a group is
held for ``settle_s`` of wall-clock time so that its stragglers can join it, then released, oldest
timestamp first. Something that still shows up for a timestamp already released is flagged
``late``; history is never reordered.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .types import Group, RawChange


class ChangeAssembler:
    def __init__(self, settle_s: float) -> None:
        self._settle_s = settle_s
        self._pending: dict[datetime, Group] = {}
        self._arrived: dict[datetime, float] = {}  # wall time the group was first seen
        self._released_up_to: datetime | None = None

    def feed(self, changes: Iterable[RawChange], now: float) -> list[Group]:
        """Add notifications; returns the groups that are ready (see ``poll``) plus late ones."""
        late: dict[datetime, Group] = {}
        for c in changes:
            if self._released_up_to is not None and c.ts <= self._released_up_to:
                late.setdefault(c.ts, Group(c.ts, late=True)).values[c.tag] = c.value
                continue
            if c.ts not in self._pending:
                self._pending[c.ts] = Group(c.ts)
                self._arrived[c.ts] = now
            self._pending[c.ts].values[c.tag] = c.value
        return [late[ts] for ts in sorted(late)] + self.poll(now)

    def poll(self, now: float) -> list[Group]:
        """Release, oldest first, every group that has waited long enough.

        Release stops at the first group that has not: a newer group never overtakes an older one.
        """
        out: list[Group] = []
        for ts in sorted(self._pending):
            if now - self._arrived[ts] < self._settle_s:
                break
            out.append(self._pending.pop(ts))
            del self._arrived[ts]
            self._released_up_to = ts
        return out

    def flush(self) -> list[Group]:
        """Release everything (shutdown)."""
        out = [self._pending[ts] for ts in sorted(self._pending)]
        if out:
            self._released_up_to = out[-1].ts
        self._pending.clear()
        self._arrived.clear()
        return out

    @property
    def oldest_pending(self) -> datetime | None:
        return min(self._pending) if self._pending else None
