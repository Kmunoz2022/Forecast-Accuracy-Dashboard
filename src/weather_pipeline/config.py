from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Station:
    station_id: str
    city: str
    state: str
    station_name: str
    latitude: float
    longitude: float
    timezone: str
    nws_station_id: str
    ncei_station_id: str
    settlement_source: str
    active: bool


REQUIRED_COLUMNS = {
    "station_id",
    "city",
    "state",
    "station_name",
    "latitude",
    "longitude",
    "timezone",
    "nws_station_id",
    "ncei_station_id",
    "settlement_source",
    "active",
}


def _parse_active(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Invalid active value: {value!r}")


def load_stations(path: Path) -> list[Station]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")

        stations: list[Station] = []
        seen_ids: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            station_id = row["station_id"].strip()
            if not station_id:
                raise ValueError(f"{path}:{line_number}: station_id is required")
            if station_id in seen_ids:
                raise ValueError(f"{path}:{line_number}: duplicate station_id {station_id!r}")
            seen_ids.add(station_id)

            timezone = row["timezone"].strip()
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(
                    f"{path}:{line_number}: unknown IANA timezone {timezone!r}"
                ) from exc

            station = Station(
                station_id=station_id,
                city=row["city"].strip(),
                state=row["state"].strip(),
                station_name=row["station_name"].strip(),
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                timezone=timezone,
                nws_station_id=row["nws_station_id"].strip(),
                ncei_station_id=row["ncei_station_id"].strip(),
                settlement_source=row["settlement_source"].strip(),
                active=_parse_active(row["active"]),
            )
            if not -90 <= station.latitude <= 90:
                raise ValueError(f"{path}:{line_number}: latitude is out of range")
            if not -180 <= station.longitude <= 180:
                raise ValueError(f"{path}:{line_number}: longitude is out of range")
            stations.append(station)

    if not stations:
        raise ValueError(f"{path} does not contain any stations")
    return stations
