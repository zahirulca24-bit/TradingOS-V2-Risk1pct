"""Strict offline snapshot/candle timing helpers for the future unified scanner.

Phase 1 provides the contract and tests. Phase 2 will wire the pipeline to it.
This module never connects to an exchange or submits orders.
"""

from datetime import datetime, timezone
from collections.abc import Mapping, Sequence


CANDLE_SECONDS = 15 * 60
MIN_RR = 2.0


def last_closed_boundary(timestamp: int | float, interval_seconds: int = CANDLE_SECONDS) -> int:
    """Unix second of the latest completed interval boundary at timestamp."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    return (int(timestamp) // interval_seconds) * interval_seconds


def parse_utc(value: str) -> datetime:
    """Parse timezone-aware ISO 8601; reject timezone-naive timestamps."""
    if not isinstance(value, str):
        raise ValueError("Timestamp must be ISO 8601 text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def is_snapshot_fresh(
    snapshot: Mapping,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 30 * 60,
) -> bool:
    """Only snapshots explicitly marked successful can be considered fresh."""
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    if not isinstance(snapshot, Mapping) or snapshot.get("status") != "success":
        return False
    if not isinstance(snapshot.get("failures"), list) or snapshot["failures"]:
        return False
    try:
        generated_at = parse_utc(snapshot["generated_at"])
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            return False
        age = (current.astimezone(timezone.utc) - generated_at).total_seconds()
        return -60 <= age <= max_age_seconds
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def is_complete_cycle(
    snapshots: Sequence[Mapping],
    *,
    required_count: int = 3,
) -> bool:
    """Return True only for matching successful, error-free scanner reports."""
    if not isinstance(snapshots, (list, tuple)) or len(snapshots) != required_count:
        return False
    try:
        cycle_ids = set()
        candle_closes = set()
        for item in snapshots:
            if not isinstance(item, Mapping) or item.get("status") != "success":
                return False
            if not isinstance(item.get("failures"), list) or item["failures"]:
                return False
            cycle_id = item.get("cycle_id")
            close = item.get("candle_close_utc")
            if not isinstance(cycle_id, str) or not cycle_id.strip():
                return False
            parsed = parse_utc(close)
            cycle_ids.add(cycle_id)
            candle_closes.add(parsed)
        return len(cycle_ids) == 1 and len(candle_closes) == 1
    except (ValueError, TypeError, KeyError):
        return False
