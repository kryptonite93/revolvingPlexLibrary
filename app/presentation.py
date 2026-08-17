from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def _local_datetime(value: datetime, timezone: str) -> datetime:
    utc_value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return utc_value.astimezone(ZoneInfo(timezone))


def format_local_datetime(value: datetime, timezone: str) -> str:
    return _local_datetime(value, timezone).strftime("%Y-%m-%d %H:%M %Z")


def format_local_date(value: datetime, timezone: str) -> str:
    return _local_datetime(value, timezone).strftime("%Y-%m-%d")
