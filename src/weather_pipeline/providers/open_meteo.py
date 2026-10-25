from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station
from weather_pipeline.http import get_json


BASE_URL = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"


def build_url(station: Station) -> str:
    query = urlencode(
        {
            "latitude": station.latitude,
            "longitude": station.longitude,
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "fahrenheit",
            "timezone": station.timezone,
            "forecast_days": 3,
        }
    )
    return f"{BASE_URL}?{query}"


def normalize_response(
    station: Station,
    payload: dict[str, Any],
    *,
    retrieved_at: datetime,
    source_url: str,
) -> dict[str, Any]:
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware")

    target_date = retrieved_at.astimezone(ZoneInfo(station.timezone)).date() + timedelta(days=1)
    daily = payload.get("daily", {})
    dates = daily.get("time", [])
    try:
        index = dates.index(target_date.isoformat())
        high = daily["temperature_2m_max"][index]
        low = daily["temperature_2m_min"][index]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"Open-Meteo response has no complete forecast for {target_date} "
            f"at {station.station_id}"
        ) from exc

    if high is None or low is None:
        raise ValueError(
            f"Open-Meteo returned a null forecast for {target_date} at {station.station_id}"
        )

    return {
        "schema_version": 1,
        "provider": "open_meteo",
        "station_id": station.station_id,
        "station_name": station.station_name,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "timezone": station.timezone,
        "snapshot_date_utc": retrieved_at.date().isoformat(),
        "retrieved_at_utc": retrieved_at.isoformat().replace("+00:00", "Z"),
        "target_date_local": target_date.isoformat(),
        "forecast_high_f": float(high),
        "forecast_low_f": float(low),
        "provider_grid_latitude": payload.get("latitude"),
        "provider_grid_longitude": payload.get("longitude"),
        "provider_elevation_m": payload.get("elevation"),
        "source_url": source_url,
        "attribution": ATTRIBUTION,
    }


def fetch_forecast(
    station: Station,
    *,
    retrieved_at: datetime,
    fetch_json: Callable[[str], Any] = get_json,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    url = build_url(station)
    payload = fetch_json(url)
    if not isinstance(payload, dict):
        raise ValueError("Open-Meteo returned a non-object response")
    record = normalize_response(
        station,
        payload,
        retrieved_at=retrieved_at,
        source_url=url,
    )
    return record, payload, url
