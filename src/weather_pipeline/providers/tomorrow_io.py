from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station
from weather_pipeline.http import get_json


BASE_URL = "https://api.tomorrow.io/v4/weather/forecast"
ATTRIBUTION = "Weather data provided by Tomorrow.io"


def build_url(station: Station) -> str:
    query = urlencode(
        {
            "location": f"{station.latitude},{station.longitude}",
            "timesteps": "1h",
            "units": "imperial",
        }
    )
    return f"{BASE_URL}?{query}"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_response(
    station: Station,
    payload: dict[str, Any],
    *,
    retrieved_at: datetime,
    source_url: str,
) -> dict[str, Any]:
    target_date = retrieved_at.astimezone(ZoneInfo(station.timezone)).date() + timedelta(days=1)
    temperatures: list[float] = []
    try:
        hourly = payload["timelines"]["hourly"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Tomorrow.io response has no hourly timeline") from exc

    for point in hourly:
        try:
            local_date = _parse_timestamp(point["time"]).astimezone(
                ZoneInfo(station.timezone)
            ).date()
            temperature = point["values"]["temperature"]
        except (KeyError, TypeError, ValueError):
            continue
        if local_date == target_date and temperature is not None:
            temperatures.append(float(temperature))

    if not temperatures:
        raise ValueError(
            f"Tomorrow.io returned no hourly temperatures for {target_date} "
            f"at {station.station_id}"
        )

    return {
        "schema_version": 1,
        "provider": "tomorrow_io",
        "station_id": station.station_id,
        "station_name": station.station_name,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "timezone": station.timezone,
        "snapshot_date_utc": retrieved_at.date().isoformat(),
        "retrieved_at_utc": retrieved_at.isoformat().replace("+00:00", "Z"),
        "target_date_local": target_date.isoformat(),
        "forecast_high_f": max(temperatures),
        "forecast_low_f": min(temperatures),
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
    api_key: str,
    fetch_json: Callable[..., Any] = get_json,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    url = build_url(station)
    payload = fetch_json(url, headers={"apikey": api_key})
    if not isinstance(payload, dict):
        raise ValueError("Tomorrow.io returned a non-object response")
    record = normalize_response(
        station,
        payload,
        retrieved_at=retrieved_at,
        source_url=url,
    )
    return record, payload, url
