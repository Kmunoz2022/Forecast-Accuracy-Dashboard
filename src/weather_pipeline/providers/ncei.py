from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

from weather_pipeline.config import Station
from weather_pipeline.http import get_json


BASE_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
ATTRIBUTION = "NOAA National Centers for Environmental Information"


def build_url(stations: Iterable[Station], start_date: date, end_date: date) -> str:
    station_ids = ",".join(station.ncei_station_id for station in stations)
    query = urlencode(
        {
            "dataset": "daily-summaries",
            "dataTypes": "TMAX,TMIN",
            "stations": station_ids,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "format": "json",
            "units": "standard",
            "includeAttributes": "true",
        }
    )
    return f"{BASE_URL}?{query}"


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def normalize_response(
    stations: Iterable[Station],
    payload: list[dict[str, Any]],
    *,
    retrieved_at: datetime,
) -> list[dict[str, Any]]:
    stations_by_ncei_id = {station.ncei_station_id: station for station in stations}
    records: list[dict[str, Any]] = []
    for source_row in payload:
        ncei_id = str(source_row.get("STATION", ""))
        station = stations_by_ncei_id.get(ncei_id)
        if station is None or not source_row.get("DATE"):
            continue

        high = _optional_float(source_row.get("TMAX"))
        low = _optional_float(source_row.get("TMIN"))
        if high is None and low is None:
            continue

        records.append(
            {
                "schema_version": 1,
                "provider": "noaa_ncei_daily_summaries",
                "station_id": station.station_id,
                "station_name": station.station_name,
                "ncei_station_id": ncei_id,
                "target_date_local": str(source_row["DATE"]),
                "actual_high_f": high,
                "actual_low_f": low,
                "complete": high is not None and low is not None,
                "tmax_attributes": source_row.get("TMAX_ATTRIBUTES"),
                "tmin_attributes": source_row.get("TMIN_ATTRIBUTES"),
                "retrieved_at_utc": retrieved_at.isoformat().replace("+00:00", "Z"),
                "source_url": BASE_URL,
                "attribution": ATTRIBUTION,
            }
        )
    return records


def fetch_observations(
    stations: list[Station],
    *,
    start_date: date,
    end_date: date,
    retrieved_at: datetime,
    fetch_json: Callable[[str], Any] = get_json,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    url = build_url(stations, start_date, end_date)
    payload = fetch_json(url)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("NCEI returned an unexpected response")
    records = normalize_response(stations, payload, retrieved_at=retrieved_at)
    return records, payload, url
