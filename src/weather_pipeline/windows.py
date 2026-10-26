"""Explicit reporting windows; market dates never come from collection time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ForecastWindow:
    target_date: date
    start: datetime
    end: datetime
    boundary: str = "local_standard_midnight_to_midnight"

    def metadata(self) -> dict:
        return {"target_date_local": self.target_date.isoformat(),
                "window_start_utc": utc_text(self.start),
                "window_end_utc": utc_text(self.end),
                "daily_boundary": self.boundary}


def standard_day(target: date, zone_name: str) -> ForecastWindow:
    local = datetime.combine(target, time(12), ZoneInfo(zone_name))
    # A fixed standard offset, not the wall-clock offset on a DST day.
    standard_offset = local.utcoffset() - local.dst()
    start = datetime.combine(target, time.min, timezone(standard_offset)).astimezone(timezone.utc)
    return ForecastWindow(target, start, start + timedelta(days=1))
