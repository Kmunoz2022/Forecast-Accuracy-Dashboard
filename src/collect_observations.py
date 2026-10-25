from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from weather_pipeline.config import load_stations
from weather_pipeline.providers.ncei import fetch_observations
from weather_pipeline.storage import upsert_jsonl, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY_FIELDS = ("provider", "station_id", "target_date_local")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect NOAA/NCEI daily station outcomes")
    parser.add_argument("--stations", type=Path, default=PROJECT_ROOT / "config/stations.csv")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--as-of",
        help="UTC retrieval timestamp for reproducible runs (ISO 8601)",
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
    if args.lookback_days < 1:
        print("--lookback-days must be at least 1", file=sys.stderr)
        return 2

    retrieved_at = parse_as_of(args.as_of)
    end_date = args.end_date or (retrieved_at.date() - timedelta(days=1))
    start_date = end_date - timedelta(days=args.lookback_days - 1)
    stations = [station for station in load_stations(args.stations) if station.active]

    try:
        records, payload, _ = fetch_observations(
            stations,
            start_date=start_date,
            end_date=end_date,
            retrieved_at=retrieved_at,
        )
    except Exception as exc:
        print(f"Observation collection failed: {exc}", file=sys.stderr)
        return 1

    collection_date = retrieved_at.date().isoformat()
    year, month, _ = collection_date.split("-")
    write_json(
        args.data_dir / "raw" / "ncei" / year / month / f"{collection_date}.json",
        payload,
    )

    records_by_date: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        records_by_date[record["target_date_local"]].append(record)

    changes = 0
    for target_date, date_records in records_by_date.items():
        target_year, target_month, _ = target_date.split("-")
        output_path = (
            args.data_dir
            / "observations"
            / target_year
            / target_month
            / f"{target_date}.jsonl"
        )
        changes += upsert_jsonl(
            output_path,
            date_records,
            key_fields=KEY_FIELDS,
            replace=True,
            ignore_compare_fields=frozenset({"retrieved_at_utc"}),
        )

    print(
        f"NOAA returned {len(records)} station-day rows for {start_date} through "
        f"{end_date}; rows changed: {changes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
