from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station
from weather_pipeline.http import get_json


BASE_URL = "https://api.openweathermap.org/data/2.5/forecast"
ATTRIBUTION = "Weather data provided by OpenWeather"


def build_urls(station: Station, api_key: str) -> tuple[str, str]:
    public_query = {
        "lat": station.latitude,
        "lon": station.longitude,
        "units": "imperial",
    }
    request_query = {**public_query, "appid": api_key}
    return (
        f"{BASE_URL}?{urlencode(request_query)}",
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
    target_points = []
    for point in payload.get("list", []):
        timestamp = point.get("dt")
        if timestamp is None:
            continue
        point_date = datetime.fromtimestamp(timestamp, tz=ZoneInfo("UTC")).astimezone(
            ZoneInfo(station.timezone)
        ).date()
        if point_date == target_date:
            target_points.append(point)

    try:
        high = max(float(point["main"]["temp_max"]) for point in target_points)
        low = min(float(point["main"]["temp_min"]) for point in target_points)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"OpenWeather returned no complete 3-hour forecast for {target_date} "
            f"at {station.station_id}"
        ) from exc

    if not target_points:
        raise ValueError(
            f"OpenWeather returned no forecast points for {target_date} at {station.station_id}"
        )

    return {
        "schema_version": 1,
        "provider": "openweathermap",
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
        "source_resolution": "3h",
        "daily_boundary": "local_midnight_to_midnight",
        "aggregation_method": "max_min_of_3_hour_points",
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
        raise ValueError("OpenWeather returned a non-object response")
    record = normalize_response(
        station,
        payload,
        retrieved_at=retrieved_at,
        source_url=public_url,
    )
    return record, payload, public_url
