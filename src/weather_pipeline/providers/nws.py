from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station
from weather_pipeline.http import get_json
from weather_pipeline.temperatures import sampled_extrema


BASE_URL = "https://api.weather.gov"
ATTRIBUTION = "NOAA National Weather Service"


def normalize_response(
    station: Station,
    payload: dict[str, Any],
    *,
    retrieved_at: datetime,
    source_url: str,
) -> dict[str, Any]:
    target_date = retrieved_at.astimezone(ZoneInfo(station.timezone)).date() + timedelta(days=1)
    properties = payload.get("properties", {})
    samples = []
    for period in properties.get("periods", []):
        timestamp = datetime.fromisoformat(period["startTime"].replace("Z", "+00:00"))
        temperature = period.get("temperature")
        unit = period.get("temperatureUnit")
        if temperature is not None:
            if unit == "C":
                temperature = float(temperature) * 9 / 5 + 32
            elif unit != "F":
                raise ValueError(f"Unsupported NWS temperature unit: {unit!r}")
        samples.append((timestamp, temperature))
    extrema = sampled_extrema(station, samples, retrieved_at=retrieved_at, step_hours=1)
    return {
        "schema_version": 1,
        "provider": "nws",
        "station_id": station.station_id,
        "station_name": station.station_name,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "timezone": station.timezone,
        "snapshot_date_utc": retrieved_at.date().isoformat(),
        "retrieved_at_utc": retrieved_at.isoformat().replace("+00:00", "Z"),
        "provider_updated_at_utc": properties.get("updateTime"),
        "target_date_local": target_date.isoformat(),
        **extrema,
        "source_resolution": "1h",
        "daily_boundary": "local_midnight_to_midnight",
        "aggregation_method": "max_min_of_hourly_points",
        "source_url": source_url,
        "attribution": ATTRIBUTION,
    }


def fetch_forecast(
    station: Station,
    *,
    retrieved_at: datetime,
    fetch_json: Callable[..., Any] = get_json,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    # Refresh the grid mapping daily: NWS offices/grid coordinates can change.
    point_url = f"{BASE_URL}/points/{station.latitude:.4f},{station.longitude:.4f}"
    headers = {"Accept": "application/geo+json"}
    point = fetch_json(point_url, headers=headers)
    url = point.get("properties", {}).get("forecastHourly") if isinstance(point, dict) else None
    if not isinstance(url, str):
        raise ValueError(f"NWS has no hourly forecast for {station.station_id}")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "api.weather.gov":
        raise ValueError("NWS returned an unexpected forecast URL")
    payload = fetch_json(url, headers=headers)
    if not isinstance(payload, dict):
        raise ValueError("NWS returned a non-object forecast")
    record = normalize_response(station, payload, retrieved_at=retrieved_at, source_url=url)
    return record, payload, url
