"""Shared query parsing: a time range that requires an explicit UTC offset on both ends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, Query


@dataclass(frozen=True, slots=True)
class TimeRange:
    frm: datetime
    to: datetime


def _require_aware(name: str, value: datetime) -> datetime:
    if value.tzinfo is None:
        raise HTTPException(
            422, f"'{name}' must include a UTC offset (e.g. a trailing 'Z'): {value.isoformat()!r}"
        )
    return value.astimezone(UTC)


async def time_range(
    frm: datetime = Query(alias="from"), to: datetime = Query(alias="to")
) -> TimeRange:
    frm, to = _require_aware("from", frm), _require_aware("to", to)
    if to < frm:
        raise HTTPException(422, "'to' must not be before 'from'")
    return TimeRange(frm, to)
