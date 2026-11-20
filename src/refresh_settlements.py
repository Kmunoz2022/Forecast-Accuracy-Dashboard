"""Refresh outcomes only for known events linked to saved live forecasts."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from collect_forecasts import parse_as_of
from collect_market_forecasts import save_contracts
from weather_pipeline.archive import forecast_targets, load_category
from weather_pipeline.config import load_stations
from weather_pipeline.kalshi import KalshiClient, load_mappings, normalize_contract, parse_time
from weather_pipeline.telemetry import failure_reason
from weather_pipeline.verification import number

ROOT = Path(__file__).resolve().parents[1]


def refresh(root, mappings, now, *, client=None, correction_days=14):
    client = client or KalshiClient()
    targets = forecast_targets(root)
    contracts, problems = load_category(root, "markets")
    by_series = {m.series: m for m in mappings}
    latest = {}
    for row in sorted(contracts, key=lambda r: parse_time(r["retrieved_at_utc"])):
        latest[row["market_ticker"]] = row
    events = defaultdict(list)
    for row in latest.values():
        if (row["station_id"], row["target_date_local"]) in targets and row["series_ticker"] in by_series:
            events[row["event_ticker"]].append(row)
    failures, refreshed = len(problems), 0
    for ticker, rows in sorted(events.items()):
        end = max(parse_time(r["window_end_utc"]) for r in rows)
        if now < end:
            continue
        finalized = all(r.get("status") == "finalized" and r.get("result") in {"yes", "no"}
                        and number(r.get("expiration_value")) is not None for r in rows)
        # Old missing settlements remain relevant indefinitely; settled values get a
        # bounded correction window. No history-wide endpoint or historical forecast call.
        if finalized and end < now - timedelta(days=correction_days):
            continue
        try:
            payload = client.get(f"events/{ticker}", with_nested_markets="true")
            event = payload["event"]
            if event.get("event_ticker") != ticker:
                raise ValueError("Wrong event")
            markets = event.get("markets") or payload.get("markets")
            if not markets:
                raise ValueError("Event has no contracts")
            mapping = by_series[rows[0]["series_ticker"]]
            series = client.series(mapping.series)
            normalized = []
            for market in markets:
                if market.get("event_ticker") != ticker:
                    raise ValueError("Wrong contract event")
                normalized.append(normalize_contract(mapping, market, series, now))
            # Preserve changed rules as new versions so scoring can flag the mismatch.
            save_contracts(root, normalized)
            if any(r["validation_errors"] for r in normalized):
                raise ValueError("Contract validation failed")
            refreshed += 1
        except Exception as exc:
            failures += 1
            print(f"Settlement refresh failed for {ticker}: {failure_reason(exc)}")
    print(f"Refreshed {refreshed} relevant events; failures: {failures}")
    return int(failures > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--stations", type=Path, default=ROOT / "config/stations.csv")
    parser.add_argument("--mappings", type=Path, default=ROOT / "config/kalshi_markets.json")
    parser.add_argument("--as-of")
    parser.add_argument("--correction-days", type=int, default=14)
    args = parser.parse_args()
    if args.correction_days < 1:
        parser.error("--correction-days must be positive")
    return refresh(args.data_dir, load_mappings(args.mappings, load_stations(args.stations)),
                   parse_as_of(args.as_of), correction_days=args.correction_days)


if __name__ == "__main__":
    raise SystemExit(main())
