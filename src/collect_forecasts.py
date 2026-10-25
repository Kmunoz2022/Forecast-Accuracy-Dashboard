from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from weather_pipeline.config import Station, load_stations
from weather_pipeline.providers.open_meteo import fetch_forecast as fetch_open_meteo
from weather_pipeline.providers.openweathermap import fetch_forecast as fetch_openweathermap
from weather_pipeline.providers.tomorrow_io import fetch_forecast as fetch_tomorrow_io
from weather_pipeline.providers.weatherbit import fetch_forecast as fetch_weatherbit
from weather_pipeline.storage import read_jsonl, upsert_jsonl, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY_FIELDS = ("provider", "station_id", "target_date_local", "snapshot_date_utc")


@dataclass(frozen=True)
class EnabledProvider:
    slug: str
    fetch: Callable[[Station], tuple[dict[str, Any], dict[str, Any], str]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect next-day Open-Meteo forecasts")
    parser.add_argument("--stations", type=Path, default=PROJECT_ROOT / "config/stations.csv")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--as-of",
        help="UTC collection timestamp for reproducible runs (ISO 8601)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Replace today's existing station snapshots",
    )
    return parser.parse_args()


def parse_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--as-of must include a UTC offset")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def configured_providers(retrieved_at: datetime) -> list[EnabledProvider]:
    providers = [
        EnabledProvider(
            slug="open_meteo",
            fetch=lambda station: fetch_open_meteo(station, retrieved_at=retrieved_at),
        )
    ]

    openweather_key = os.getenv("OPENWEATHERMAP_API_KEY")
    if openweather_key:
        providers.append(
            EnabledProvider(
                slug="openweathermap",
                fetch=lambda station: fetch_openweathermap(
                    station,
                    retrieved_at=retrieved_at,
                    api_key=openweather_key,
                ),
            )
        )
    else:
        print("OPENWEATHERMAP_API_KEY is not set; skipping OpenWeather")

    weatherbit_key = os.getenv("WEATHERBIT_API_KEY")
    if weatherbit_key and _enabled(os.getenv("WEATHERBIT_ARCHIVAL_ALLOWED")):
        providers.append(
            EnabledProvider(
                slug="weatherbit",
                fetch=lambda station: fetch_weatherbit(
                    station,
                    retrieved_at=retrieved_at,
                    api_key=weatherbit_key,
                ),
            )
        )
    elif weatherbit_key:
        print(
            "WEATHERBIT_API_KEY is set, but archival collection is disabled. "
            "Set WEATHERBIT_ARCHIVAL_ALLOWED=true only for an active plan that permits storage."
        )

    tomorrow_key = os.getenv("TOMORROW_API_KEYS")
    if tomorrow_key and _enabled(os.getenv("TOMORROW_ARCHIVAL_ALLOWED")):
        providers.append(
            EnabledProvider(
                slug="tomorrow_io",
                fetch=lambda station: fetch_tomorrow_io(
                    station,
                    retrieved_at=retrieved_at,
                    api_key=tomorrow_key,
                ),
            )
        )
    elif tomorrow_key:
        print(
            "TOMORROW_API_KEYS is set, but archival collection is disabled. "
            "Set TOMORROW_ARCHIVAL_ALLOWED=true after confirming retention rights."
        )

    if os.getenv("GOOGLE_MAPS_DEMO_API_KEY"):
        print("Google Weather is not archived because daily forecast retention is limited to 24 hours.")
    if os.getenv("WEATHERAPI_API_KEY"):
        print("WeatherAPI is not archived because forecast retention is limited to 24 hours.")

    return providers


def main() -> int:
    args = parse_args()
    retrieved_at = parse_as_of(args.as_of)
    stations = [station for station in load_stations(args.stations) if station.active]
    providers = configured_providers(retrieved_at)
    snapshot_date = retrieved_at.date().isoformat()
    year, month, _ = snapshot_date.split("-")
    output_path = args.data_dir / "forecasts" / year / month / f"{snapshot_date}.jsonl"
    existing_keys = {
        tuple(row[field] for field in KEY_FIELDS) for row in read_jsonl(output_path)
    }

    records = []
    failures: list[str] = []
    for provider in providers:
        for station in stations:
            local_target = (
                retrieved_at.astimezone(ZoneInfo(station.timezone)).date()
                + timedelta(days=1)
            ).isoformat()
            expected_key = (provider.slug, station.station_id, local_target, snapshot_date)
            if expected_key in existing_keys and not args.refresh:
                print(
                    f"Skipping existing {provider.slug} snapshot for "
                    f"{station.station_id} ({local_target})"
                )
                continue
            try:
                record, payload, _ = provider.fetch(station)
                records.append(record)
                raw_path = (
                    args.data_dir
                    / "raw"
                    / provider.slug
                    / year
                    / month
                    / snapshot_date
                    / f"{station.station_id}.json"
                )
                write_json(raw_path, payload)
                print(
                    f"Collected {provider.slug} {station.station_id} forecast "
                    f"for {record['target_date_local']}"
                )
            except Exception as exc:  # continue so one location cannot hide other successes
                failures.append(f"{provider.slug}/{station.station_id}: {exc}")

    changes = upsert_jsonl(
        output_path,
        records,
        key_fields=KEY_FIELDS,
        replace=args.refresh,
        ignore_compare_fields=frozenset({"retrieved_at_utc"}),
    )
    print(f"Forecast rows changed: {changes}")
    if failures:
        print("Forecast collection failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
