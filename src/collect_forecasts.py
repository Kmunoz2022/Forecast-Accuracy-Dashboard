from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from weather_pipeline.config import load_stations
from weather_pipeline.providers.open_meteo import fetch_forecast
from weather_pipeline.storage import read_jsonl, upsert_jsonl, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY_FIELDS = ("provider", "station_id", "target_date_local", "snapshot_date_utc")


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


def main() -> int:
    args = parse_args()
    retrieved_at = parse_as_of(args.as_of)
    stations = [station for station in load_stations(args.stations) if station.active]
    snapshot_date = retrieved_at.date().isoformat()
    year, month, _ = snapshot_date.split("-")
    output_path = args.data_dir / "forecasts" / year / month / f"{snapshot_date}.jsonl"
    existing_keys = {
        tuple(row[field] for field in KEY_FIELDS) for row in read_jsonl(output_path)
    }

    records = []
    failures: list[str] = []
    for station in stations:
        local_target = (
            retrieved_at.astimezone(ZoneInfo(station.timezone)).date() + timedelta(days=1)
        ).isoformat()
        expected_key = ("open_meteo", station.station_id, local_target, snapshot_date)
        if expected_key in existing_keys and not args.refresh:
            print(f"Skipping existing snapshot for {station.station_id} ({local_target})")
            continue
        try:
            record, payload, _ = fetch_forecast(station, retrieved_at=retrieved_at)
            records.append(record)
            raw_path = (
                args.data_dir
                / "raw"
                / "open_meteo"
                / year
                / month
                / snapshot_date
                / f"{station.station_id}.json"
            )
            write_json(raw_path, payload)
            print(f"Collected {station.station_id} forecast for {record['target_date_local']}")
        except Exception as exc:  # continue so one location cannot hide other successes
            failures.append(f"{station.station_id}: {exc}")

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
