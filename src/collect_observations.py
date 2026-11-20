from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from weather_pipeline.archive import forecast_targets, load_category
from weather_pipeline.config import load_stations
from weather_pipeline.providers.ncei import fetch_observations
from weather_pipeline.storage import upsert_jsonl, write_json
from weather_pipeline.verification import number
from weather_pipeline.windows import local_day, standard_day


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KEY_FIELDS = ("provider", "station_id", "target_date_local")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect NOAA/NCEI daily station outcomes")
    parser.add_argument("--stations", type=Path, default=PROJECT_ROOT / "config/stations.csv")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--save-raw", action="store_true", help="Also archive full API responses for debugging"
    )
    parser.add_argument("--lookback-days", type=int, default=14,
                        help="Refresh corrections within this many days, only for saved forecast targets")
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


def needed_requests(root, stations, now, *, end_date, lookback_days):
    """Batch contiguous requested dates only when the exact station set is identical."""
    existing, _ = load_category(root, "observations")
    complete = {(r["station_id"], r["target_date_local"]) for r in existing
                if all(number(r.get(f"actual_{metric}_f")) is not None for metric in ("high", "low"))}
    by_id = {s.station_id: s for s in stations}
    correction_start = end_date - timedelta(days=lookback_days - 1)
    dates = defaultdict(set)
    for station_id, target in forecast_targets(root):
        station = by_id.get(station_id)
        day = date.fromisoformat(target)
        if not station or day > end_date:
            continue
        # Both reporting windows must have ended; UTC yesterday can still be today locally.
        if max(local_day(day, station.timezone).end, standard_day(day, station.timezone).end) > now:
            continue
        if (station_id, target) not in complete or day >= correction_start:
            dates[day].add(station_id)
    requests = []
    for day, ids in sorted(dates.items()):
        ids = tuple(sorted(ids))
        if requests and requests[-1][1] + timedelta(days=1) == day and requests[-1][2] == ids:
            requests[-1] = (requests[-1][0], day, ids)
        else:
            requests.append((day, day, ids))
    return [(start, end, [by_id[station_id] for station_id in ids]) for start, end, ids in requests]


def main() -> int:
    args = parse_args()
    if args.lookback_days < 1:
        print("--lookback-days must be at least 1", file=sys.stderr)
        return 2

    retrieved_at = parse_as_of(args.as_of)
    end_date = args.end_date or (retrieved_at.date() - timedelta(days=1))
    stations = [station for station in load_stations(args.stations) if station.active]
    if not stations:
        print("No active stations; no requests made.")
        return 0

    requests = needed_requests(args.data_dir, stations, retrieved_at,
                               end_date=end_date, lookback_days=args.lookback_days)
    if not requests:
        print("No completed saved forecast targets need observations; no requests made.")
        return 0
    failures, returned = 0, 0
    records_by_date: dict[str, list[dict]] = defaultdict(list)
    for start_date, request_end, request_stations in requests:
        try:
            records, payload, _ = fetch_observations(
                request_stations, start_date=start_date, end_date=request_end, retrieved_at=retrieved_at)
            ids = {s.station_id for s in request_stations}
            for record in records:
                if (record["station_id"] in ids and
                        start_date.isoformat() <= record["target_date_local"] <= request_end.isoformat()):
                    records_by_date[record["target_date_local"]].append(record)
                    returned += 1
            if args.save_raw:
                collection_date = retrieved_at.date().isoformat()
                year, month, _ = collection_date.split("-")
                ncei_ids = {s.ncei_station_id for s in request_stations}
                selected = [r for r in payload if r.get("STATION") in ncei_ids and
                            start_date.isoformat() <= r.get("DATE", "") <= request_end.isoformat()]
                write_json(args.data_dir / "raw/ncei" / year / month / collection_date /
                           f"{start_date}_{request_end}.json", selected)
        except Exception as exc:
            failures += 1
            print(f"Observation collection failed for {start_date} through {request_end}: {exc}", file=sys.stderr)

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

    print(f"NOAA returned {returned} relevant station-day rows; rows changed: {changes}")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
