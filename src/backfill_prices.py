"""Download Kalshi bracket prices at a fixed moment for each settled market.

For every market in data/history/kalshi/<series>.csv, records the yes bid/ask
from the hourly candle ending at the start of the measurement day (local
standard midnight), minus --hours-before. That is roughly when day-ahead
forecasts were last updated, so model and market see similar information.
Output: data/history/kalshi_prices/<series>.csv. Finished tickers are skipped.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

from backfill_history import write_csv
from weather_pipeline.config import load_stations
from weather_pipeline.kalshi import KalshiClient
from weather_pipeline.windows import standard_day


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["market_ticker", "date", "snapshot_ts", "yes_bid", "yes_ask", "last_price", "volume"]


def candle_value(candle: dict, side: str) -> str:
    # Historical candles use "close"; live candles use "close_dollars".
    values = candle.get(side) or {}
    value = values.get("close_dollars", values.get("close"))
    return "" if value is None else value


def price_row(client: KalshiClient, series: str, market: dict, zone: str, hours_before: int) -> dict:
    window = standard_day(date.fromisoformat(market["date"]), zone)
    snapshot = int((window.start - timedelta(hours=hours_before)).timestamp())
    query = {"start_ts": snapshot - 3600, "end_ts": snapshot, "period_interval": 60}
    ticker = market["market_ticker"]
    try:
        candles = client.get(f"historical/markets/{ticker}/candlesticks", **query)["candlesticks"]
    except Exception:
        candles = client.get(f"series/{series}/markets/{ticker}/candlesticks", **query)["candlesticks"]
    last = candles[-1] if candles else {}
    return {"market_ticker": ticker, "date": market["date"], "snapshot_ts": snapshot,
            "yes_bid": candle_value(last, "yes_bid"), "yes_ask": candle_value(last, "yes_ask"),
            "last_price": candle_value(last, "price"),
            "volume": last.get("volume_fp", last.get("volume", ""))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--series", nargs="+", required=True, help="e.g. KXHIGHNY KXHIGHCHI")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--hours-before", type=int, default=0)
    parser.add_argument("--history-dir", type=Path, default=PROJECT_ROOT / "data/history")
    parser.add_argument("--stations", type=Path, default=PROJECT_ROOT / "config/stations.csv")
    args = parser.parse_args()
    zones = {station.station_id: station.timezone for station in load_stations(args.stations)}
    client = KalshiClient()
    failures = 0
    for series in args.series:
        with (args.history_dir / "kalshi" / f"{series}.csv").open(newline="", encoding="utf-8") as handle:
            # Legacy 2021-2022 markets lack strike types and are not comparable.
            markets = [m for m in csv.DictReader(handle) if m["strike_type"] and m["date"]
                       and args.start.isoformat() <= m["date"] <= args.end.isoformat()]
        output = args.history_dir / "kalshi_prices" / f"{series}.csv"
        rows = {}
        if output.exists():
            with output.open(newline="", encoding="utf-8") as handle:
                rows = {row["market_ticker"]: row for row in csv.DictReader(handle)}
        for count, market in enumerate(m for m in markets if m["market_ticker"] not in rows):
            try:
                rows[market["market_ticker"]] = price_row(
                    client, series, market, zones[market["station_id"]], args.hours_before)
            except Exception as exc:
                failures += 1
                print(f"{market['market_ticker']}: {exc}", file=sys.stderr)
            if count % 200 == 199:  # checkpoint so interruptions lose little work
                write_csv(output, FIELDS, sorted(rows.values(), key=lambda row: row["market_ticker"]))
        write_csv(output, FIELDS, sorted(rows.values(), key=lambda row: row["market_ticker"]))
        print(f"{series}: {len(rows)} priced markets")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
