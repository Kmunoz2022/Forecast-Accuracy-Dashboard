"""Read-only Kalshi discovery with explicit station/date/rule validation."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from weather_pipeline.config import Station
from weather_pipeline.http import get_json
from weather_pipeline.windows import ForecastWindow, standard_day, utc_text

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
MONTHS = {name: i for i, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Market timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MarketMapping:
    station: Station
    series: str
    metric: str
    climate_code: str


@dataclass
class MarketEvent:
    mapping: MarketMapping
    ticker: str
    opens: datetime
    window: ForecastWindow
    contracts: list[dict]


def load_mappings(path: Path, stations: list[Station]) -> list[MarketMapping]:
    by_id = {station.station_id: station for station in stations if station.active}
    result = []
    seen = set()
    for row in json.loads(path.read_text(encoding="utf-8")):
        station = by_id[row["station_id"]]
        if not re.fullmatch(r"CLI[A-Z]{3}", row["climate_code"]):
            raise ValueError("Invalid climate-report code")
        for metric in ("high", "low"):
            series = row[f"{metric}_series"]
            if series in seen or not re.fullmatch(r"[A-Z0-9]+", series):
                raise ValueError(f"Invalid/duplicate market series: {series}")
            seen.add(series)
            result.append(MarketMapping(station, series, metric, row["climate_code"]))
    return result


def event_date(event_ticker: str, series: str) -> date:
    match = re.fullmatch(re.escape(series) + r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not match or match[2] not in MONTHS:
        raise ValueError(f"Unsupported daily event ticker: {event_ticker}")
    return date(2000 + int(match[1]), MONTHS[match[2]], int(match[3]))


class KalshiClient:
    def __init__(self, fetch_json: Callable = get_json, sleep: Callable = time.sleep):
        self.fetch_json = fetch_json
        self.sleep = sleep
        self.series_cache: dict[str, dict] = {}

    def get(self, path: str, **query) -> dict:
        self.sleep(0.25)  # modest sequential discovery traffic; HTTP handles 429 backoff
        result = self.fetch_json(f"{BASE_URL}/{path}" + (f"?{urlencode(query)}" if query else ""))
        if not isinstance(result, dict):
            raise ValueError("Unexpected Kalshi response")
        return result

    def series(self, ticker: str) -> dict:
        # Refreshed in every workflow run; reused during short opening-time polls.
        if ticker not in self.series_cache:
            result = self.get(f"series/{ticker}")["series"]
            if result.get("ticker") != ticker:
                raise ValueError("Kalshi returned the wrong series")
            self.series_cache[ticker] = result
        return self.series_cache[ticker]

    def markets(self, ticker: str, now: datetime) -> list[dict]:
        rows, seen = [], set()
        query = {"series_ticker": ticker, "limit": 200,
                 "min_close_ts": int((now - timedelta(days=2)).timestamp())}
        while True:
            payload = self.get("markets", **query)
            rows.extend(payload["markets"])
            cursor = payload.get("cursor")
            if not cursor:
                return rows
            if cursor in seen:
                raise ValueError("Kalshi repeated a pagination cursor")
            seen.add(cursor)
            query["cursor"] = cursor


def normalize_contract(mapping: MarketMapping, market: dict, series: dict, now: datetime) -> dict:
    target = event_date(market["event_ticker"], mapping.series)
    window = standard_day(target, mapping.station.timezone)
    opens, closes = parse_time(market["open_time"]), parse_time(market["close_time"])
    primary = market.get("rules_primary", "")
    metric_word = "maximum" if mapping.metric == "high" else "minimum"
    expected_date = f"{list(MONTHS)[target.month - 1].title()} {target.day}, {target.year}"
    source_match = re.search(r"according to (.+?), then", primary)
    source = source_match[1].strip() if source_match else "unknown"
    errors = []
    if f"({mapping.climate_code})" not in primary:
        errors.append("station_climate_code_mismatch")
    if f"{metric_word} temperature" not in primary.lower():
        errors.append("temperature_metric_mismatch")
    if f"for {expected_date}," not in primary:
        errors.append("contract_date_not_verified")
    if "fahrenheit" not in primary.lower():
        errors.append("temperature_unit_not_verified")
    if source not in {"The Weather Company", "National Weather Service"}:
        errors.append("unrecognized_settlement_source")
    sources = series.get("settlement_sources", [])
    if not any(item.get("name", "").strip() == source for item in sources):
        errors.append("series_and_contract_source_disagree")
    if closes != window.end:
        errors.append("close_time_does_not_match_configured_standard_day")
    if opens >= window.start:
        errors.append("market_does_not_open_before_measurement_day")
    if not market["ticker"].startswith(market["event_ticker"] + "-"):
        errors.append("contract_event_mismatch")
    record = {
        "schema_version": 1, "series_ticker": mapping.series,
        "market_ticker": market["ticker"], "event_ticker": market["event_ticker"],
        "station_id": mapping.station.station_id, "nws_station_id": mapping.station.nws_station_id,
        "climate_code": mapping.climate_code, "temperature_metric": mapping.metric,
        "market_open_time_utc": utc_text(opens), "market_close_time_utc": utc_text(closes),
        "title": market.get("title"), "rules_primary": primary,
        "rules_secondary": market.get("rules_secondary", ""),
        "settlement_source_name": source, "settlement_sources": sources,
        "contract_terms_url": series.get("contract_terms_url"),
        "window_basis": "configured_standard_day_checked_against_contract_close",
        "source_url": f"{BASE_URL}/markets/{market['ticker']}",
        "validation_errors": errors, **window.metadata(),
    }
    record["rules_version"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    record.update({key: market.get(key) for key in (
        "status", "result", "expiration_value", "settlement_ts", "strike_type", "floor_strike", "cap_strike")})
    record["retrieved_at_utc"] = utc_text(now)
    return record


def discover(client: KalshiClient, mappings: list[MarketMapping], now: datetime):
    events, records, failures = [], [], []
    for mapping in mappings:
        try:
            series = client.series(mapping.series)
            groups: dict[str, list[dict]] = {}
            for market in client.markets(mapping.series, now):
                record = normalize_contract(mapping, market, series, now)
                records.append(record)
                groups.setdefault(record["event_ticker"], []).append(record)
            for ticker, contracts in groups.items():
                # A changed station/source/window or differing bracket opening times needs review.
                if any(row["validation_errors"] for row in contracts):
                    failures.append(f"{ticker}: contract validation failed; inspect data/markets")
                    continue
                opening_times = {row["market_open_time_utc"] for row in contracts}
                if len(opening_times) != 1:
                    failures.append(f"{ticker}: inconsistent bracket opening times")
                    continue
                events.append(MarketEvent(mapping, ticker, parse_time(next(iter(opening_times))),
                                          standard_day(date.fromisoformat(contracts[0]["target_date_local"]),
                                                       mapping.station.timezone), contracts))
        except Exception as exc:
            failures.append(f"{mapping.series}: {exc}")
    return events, records, failures
