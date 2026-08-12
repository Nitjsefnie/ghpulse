"""Shared range, bucket, timestamp, and timing helpers for read endpoints.

The dashboard deliberately keeps the range geometry in one place.  SQL event
timestamps are UTC and bucket boundaries are epoch-aligned, while the final
bucket is clipped to the selected right edge so the client never has to infer
the visible extent from a representative timestamp.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import re
import time

from fastapi import HTTPException


log = logging.getLogger("ghpulse.api")
TIMING_ON = os.environ.get("GHPULSE_TIMING", "").lower() not in {
    "", "0", "false", "no",
}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_RANGE_RE = re.compile(r"^[1-9][0-9]*[dh]$")
_BUCKET_CANDIDATES_S = (
    60,
    5 * 60,
    15 * 60,
    30 * 60,
    3600,
    6 * 3600,
    12 * 3600,
    86400,
)


class Phases:
    """Collect labelled phase timings and emit one optional timing line."""

    __slots__ = ("_name", "_marks", "_t0")

    def __init__(self, name: str) -> None:
        self._name = name
        self._marks: list[tuple[str, float]] = []
        self._t0 = time.perf_counter()

    @contextmanager
    def step(self, label: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self._marks.append((label, time.perf_counter() - started))

    def mark(self, label: str, seconds: float) -> None:
        self._marks.append((label, seconds))

    def execute(self, label: str, cursor, query, args=None):
        started = time.perf_counter()
        try:
            if args is None:
                return cursor.execute(query)
            return cursor.execute(query, args)
        finally:
            self._marks.append((label, time.perf_counter() - started))

    def done(self, **extra) -> None:
        if not TIMING_ON:
            return
        total_ms = (time.perf_counter() - self._t0) * 1000
        parts = " ".join(f"{key}={value * 1000:.0f}ms" for key, value in self._marks)
        tail = " ".join(f"{key}={value}" for key, value in extra.items())
        log.info("TIMING %s total=%.0fms %s %s", self._name, total_ms, parts, tail)


@dataclass(frozen=True)
class RangeWindow:
    """A selected half-open UTC range and its server-side bucket width."""

    name: str
    start: datetime
    end: datetime
    bucket_s: int


def _parse_range(value: str) -> timedelta:
    """Parse Claudit-compatible ``Nd``/``Nh`` ranges.

    ``all`` returns the elapsed time since the Unix epoch for compatibility
    with Claudit callers.  The dashboard still resolves its actual left edge
    from the earliest current creation/outcome event.  A malformed range is a
    client error (422), matching FastAPI's validation errors for the other
    query parameters.
    """
    if value == "all":
        return datetime.now(timezone.utc) - _EPOCH
    if not isinstance(value, str) or not _RANGE_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"bad range: {value!r}")
    try:
        number = int(value[:-1])
        delta = timedelta(days=number) if value.endswith("d") else timedelta(hours=number)
    except (OverflowError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"bad range: {value!r}") from exc
    if delta <= timedelta(0):
        raise HTTPException(status_code=422, detail=f"bad range: {value!r}")
    return delta


def _bucket_seconds(delta: timedelta) -> int:
    """Choose the largest Claudit bucket that still gives at least 100 bins."""
    span_s = max(1, int(delta.total_seconds()))
    chosen = _BUCKET_CANDIDATES_S[0]
    for candidate in _BUCKET_CANDIDATES_S:
        if span_s / candidate < 100:
            break
        chosen = candidate
    return chosen


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def epoch_seconds(value: datetime) -> int:
    return int((as_utc(value) - _EPOCH).total_seconds())


def bucket_epoch(value: datetime, bucket_s: int) -> int:
    """Return the epoch-aligned start for a timestamp's bucket."""
    return epoch_seconds(value) // bucket_s * bucket_s


def dense_bucket_bounds(
    start: datetime,
    end: datetime,
    bucket_s: int,
) -> list[tuple[datetime, datetime, int]]:
    """Build dense epoch-aligned bins, clipping only the final right edge.

    SQL grouping remains epoch-aligned, but both visible edges are clipped to
    the selected range. Event predicates still enforce the selected half-open
    range, so the first partial bucket cannot absorb an event before ``start``.
    """
    start = as_utc(start)
    end = as_utc(end)
    if end <= start:
        return []
    first_epoch = bucket_epoch(start, bucket_s)
    last_epoch = bucket_epoch(end - timedelta(microseconds=1), bucket_s)
    out: list[tuple[datetime, datetime, int]] = []
    current_epoch = first_epoch
    while current_epoch <= last_epoch:
        current = _EPOCH + timedelta(seconds=current_epoch)
        left = max(current, start)
        right = min(current + timedelta(seconds=bucket_s), end)
        out.append((left, right, current_epoch))
        current_epoch += bucket_s
    return out


def response_bucket(start: datetime, end: datetime, counts: dict[str, int]) -> dict:
    """Serialize one public aggregate bucket without raw database columns."""
    return {
        "ts": _iso(start),
        "start": _iso(start),
        "end": _iso(end),
        **{key: int(value) for key, value in counts.items()},
    }
