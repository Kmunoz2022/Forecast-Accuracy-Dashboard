"""Live Pirate Weather forecasts; never request Time Machine/reanalysis data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station
from weather_pipeline.http import get_json
from weather_pipeline.temperatures import sampled_extrema
from weather_pipeline.windows import ForecastWindow, local_day, utc_text

BASE_URL = "https://api.pirateweather.net/forecast"
ATTRIBUTION = "Weather data provided by Pirate Weather (https://pirateweather.net/)"


def build_urls(station: Station, api_key: str) -> tuple[str, str]:
    # No timestamp in the path: this must always request the current forecast.
    query = urlencode({
        "units": "us", "extend": "hourly",
        "exclude": "currently,minutely,daily,alerts", "version": "2",
    })
    location = f"{station.latitude},{station.longitude}"
    return (
        f"{BASE_URL}/{quote(api_key, safe='')}/{location}?{query}",
        f"{BASE_URL}/REDACTED/{location}?{query}",
    )


def normalize_response(
    station: Station, payload: dict[str, Any], *, retrieved_at: datetime,
    source_url: str, window: ForecastWindow | None = None,
) -> dict[str, Any]:
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")
    window = window or local_day(
        retrieved_at.astimezone(ZoneInfo(station.timezone)).date() + timedelta(days=1),
        station.timezone,
    )
    if window.start <= retrieved_at:
        raise ValueError("Pirate Weather requires a reporting window that has not started")
    if payload.get("flags", {}).get("units", "us") != "us":
        raise ValueError("Pirate Weather did not return Fahrenheit temperatures")
    hourly = payload.get("hourly", {}).get("data")
    if not isinstance(hourly, list):
        raise ValueError("Pirate Weather response has no hourly forecast")
    samples = []
    for point in hourly:
        try:
            timestamp = datetime.fromtimestamp(point["time"], tz=timezone.utc)
            temperature = point.get("temperature")
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ValueError("Pirate Weather returned an invalid hourly point") from None
        # Pirate Weather uses -999 as a missing-data sentinel in model output.
        if temperature == -999:
            temperature = None
        samples.append((timestamp, temperature))
    extrema = sampled_extrema(station, samples, retrieved_at=retrieved_at,
                              step_hours=1, window=window)
    return {
        "schema_version": 1, "provider": "pirate_weather",
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
    if window is not None and window.start <= retrieved_at:
        raise ValueError("Pirate Weather requires a reporting window that has not started")
    request_url, public_url = build_urls(station, api_key)
    payload = fetch_json(request_url, redacted_url=public_url)
    if not isinstance(payload, dict):
        raise ValueError("Pirate Weather returned a non-object response")
    record = normalize_response(station, payload, retrieved_at=retrieved_at,
                                source_url=public_url, window=window)
    return record, payload, public_url
