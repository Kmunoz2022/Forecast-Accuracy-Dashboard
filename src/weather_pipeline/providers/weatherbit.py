from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station
from weather_pipeline.http import get_json


BASE_URL = "https://api.weatherbit.io/v2.0/forecast/daily"
ATTRIBUTION = "Weather data provided by Weatherbit"


def build_urls(station: Station, api_key: str) -> tuple[str, str]:
    public_query = {
        "lat": station.latitude,
        "lon": station.longitude,
        "days": 3,
        "units": "I",
    }
    return (
        f"{BASE_URL}?{urlencode({**public_query, 'key': api_key})}",
        f"{BASE_URL}?{urlencode(public_query)}",
    )


def normalize_response(
    station: Station,
    payload: dict[str, Any],
    *,
    retrieved_at: datetime,
    source_url: str,
) -> dict[str, Any]:
    target_date = retrieved_at.astimezone(ZoneInfo(station.timezone)).date() + timedelta(days=1)
    try:
        day = next(
            item for item in payload["data"] if item.get("valid_date") == target_date.isoformat()
        )
        high = float(day["max_temp"])
        low = float(day["min_temp"])
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        raise ValueError(
            f"Weatherbit returned no complete forecast for {target_date} at {station.station_id}"
        ) from exc

    return {
        "schema_version": 1,
        "provider": "weatherbit",
        "station_id": station.station_id,
        "station_name": station.station_name,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "timezone": station.timezone,
        "snapshot_date_utc": retrieved_at.date().isoformat(),
        "retrieved_at_utc": retrieved_at.isoformat().replace("+00:00", "Z"),
        "target_date_local": target_date.isoformat(),
        "forecast_high_f": high,
        "forecast_low_f": low,
        "source_resolution": "daily",
        "daily_boundary": "local_midnight_to_midnight",
        "aggregation_method": "provider_daily_max_min",
        "source_url": source_url,
        "attribution": ATTRIBUTION,
    }


def fetch_forecast(
    station: Station,
    *,
    retrieved_at: datetime,
    api_key: str,
    fetch_json: Callable[..., Any] = get_json,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    request_url, public_url = build_urls(station, api_key)
    payload = fetch_json(request_url, redacted_url=public_url)
    if not isinstance(payload, dict):
        raise ValueError("Weatherbit returned a non-object response")
    record = normalize_response(
        station,
        payload,
        retrieved_at=retrieved_at,
        source_url=public_url,
    )
    return record, payload, public_url
