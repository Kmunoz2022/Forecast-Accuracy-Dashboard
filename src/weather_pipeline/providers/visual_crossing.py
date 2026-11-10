"""Targeted live Timeline forecasts, excluding observations and historical APIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station
from weather_pipeline.http import get_json
from weather_pipeline.temperatures import sampled_extrema
from weather_pipeline.windows import ForecastWindow, local_day, utc_text

BASE_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
ATTRIBUTION = "Weather data provided by Visual Crossing (https://www.visualcrossing.com/)"


def requested_window(
    station: Station, retrieved_at: datetime, window: ForecastWindow | None,
) -> ForecastWindow:
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    result = window or local_day(
        retrieved_at.astimezone(ZoneInfo(station.timezone)).date() + timedelta(days=1),
        station.timezone,
    )
    if result.start <= retrieved_at:
        raise ValueError("Visual Crossing requires a reporting window that has not started")
    return result


def build_urls(
    station: Station, api_key: str, *, retrieved_at: datetime,
    window: ForecastWindow | None = None,
) -> tuple[str, str]:
    window = requested_window(station, retrieved_at, window)
    zone = ZoneInfo(station.timezone)
    start = window.start.astimezone(zone).date().isoformat()
    # A standard-time summer day includes midnight on the following civil day.
    end = (window.end - timedelta(seconds=1)).astimezone(zone).date().isoformat()
    base = f"{BASE_URL}/{station.latitude},{station.longitude}/{start}/{end}"
    query = {
        "unitGroup": "us", "contentType": "json", "include": "hours,fcst",
        "elements": "datetime,datetimeEpoch,temp,source",
    }
    return f"{base}?{urlencode({**query, 'key': api_key})}", f"{base}?{urlencode(query)}"


def normalize_response(
    station: Station, payload: dict[str, Any], *, retrieved_at: datetime,
    source_url: str, window: ForecastWindow | None = None,
) -> dict[str, Any]:
    window = requested_window(station, retrieved_at, window)
    days = payload.get("days")
    if not isinstance(days, list):
        raise ValueError("Visual Crossing response has no forecast days")
    samples = []
    for day in days:
        for point in day.get("hours", []):
            try:
                timestamp = datetime.fromtimestamp(point["datetimeEpoch"], tz=timezone.utc)
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                raise ValueError("Visual Crossing returned an invalid hourly timestamp") from None
            if not window.start <= timestamp < window.end:
                continue
            # Never let historical observations, reanalysis, or statistical normals
            # masquerade as a forecast known before the measurement day.
            if point.get("source") != "fcst":
                raise ValueError("Visual Crossing returned a non-forecast hour in the reporting window")
            samples.append((timestamp, point.get("temp")))
    extrema = sampled_extrema(station, samples, retrieved_at=retrieved_at,
                              step_hours=1, window=window)
    return {
        "schema_version": 1, "provider": "visual_crossing",
        "station_id": station.station_id, "station_name": station.station_name,
        "latitude": station.latitude, "longitude": station.longitude,
        "timezone": station.timezone,
        "snapshot_date_utc": retrieved_at.astimezone(timezone.utc).date().isoformat(),
        "retrieved_at_utc": utc_text(retrieved_at),
        **window.metadata(), **extrema,
        "source_resolution": "1h", "aggregation_method": "max_min_of_hourly_points",
        "source_url": source_url, "attribution": ATTRIBUTION,
    }


def fetch_forecast(
    station: Station, *, retrieved_at: datetime, api_key: str,
    fetch_json: Callable[..., Any] = get_json, window: ForecastWindow | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    request_url, public_url = build_urls(station, api_key, retrieved_at=retrieved_at, window=window)
    payload = fetch_json(request_url, redacted_url=public_url)
    if not isinstance(payload, dict):
        raise ValueError("Visual Crossing returned a non-object response")
    record = normalize_response(station, payload, retrieved_at=retrieved_at,
                                source_url=public_url, window=window)
    return record, payload, public_url
