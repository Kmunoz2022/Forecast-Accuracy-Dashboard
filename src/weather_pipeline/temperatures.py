"""Validate sampled temperatures before deriving local-calendar-day extrema."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station


def sampled_extrema(
    station: Station,
    samples: Iterable[tuple[datetime, Any]],
    *,
    retrieved_at: datetime,
    step_hours: int,
) -> dict[str, Any]:
    """Require every UTC-aligned sample in tomorrow's local day, including DST."""
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    if step_hours not in {1, 3}:
        raise ValueError("Only hourly and three-hourly samples are supported")
    zone = ZoneInfo(station.timezone)
    target = retrieved_at.astimezone(zone).date() + timedelta(days=1)
    start = datetime.combine(target, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(target + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    step_seconds = step_hours * 3600
    first = math.ceil(start.timestamp() / step_seconds) * step_seconds
    expected = set(range(first, int(end.timestamp()), step_seconds))
    temperatures: dict[int, float] = {}
    for timestamp, value in samples:
        if timestamp.tzinfo is None:
            raise ValueError("Forecast sample timestamp must include an offset")
        instant = timestamp.timestamp()
        if not start.timestamp() <= instant < end.timestamp():
            continue
        if instant not in expected:
            raise ValueError(f"Unexpected sample time for {station.station_id}: {timestamp}")
        if value is None:
            continue
        temperature = float(value)
        if not math.isfinite(temperature):
            raise ValueError(f"Non-finite temperature for {station.station_id}")
        if instant in temperatures and temperatures[instant] != temperature:
            raise ValueError(f"Conflicting duplicate temperature for {station.station_id}")
        temperatures[int(instant)] = temperature

    if temperatures.keys() != expected:
        raise ValueError(
            f"Incomplete {step_hours}h forecast for {station.station_id} on {target}: "
            f"{len(temperatures)}/{len(expected)} samples"
        )
    return {
        "forecast_high_f": max(temperatures.values()),
        "forecast_low_f": min(temperatures.values()),
        "sample_count": len(temperatures),
        "expected_sample_count": len(expected),
    }
