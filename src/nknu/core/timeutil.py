"""All calendar dates are Asia/Taipei days; timestamps are stored as UTC ISO-8601."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import tzdata  # noqa: F401  (the PyPI tz database must be present; the lab machine may lack system zoneinfo)

TAIPEI = ZoneInfo("Asia/Taipei")


def now_utc() -> datetime:
    return datetime.now(UTC)


def today_tpe(now: datetime | None = None) -> date:
    return (now or now_utc()).astimezone(TAIPEI).date()


def iso_now(now: datetime | None = None) -> str:
    return (now or now_utc()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
