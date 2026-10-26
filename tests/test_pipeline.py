from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from weather_pipeline.config import load_stations  # noqa: E402
from weather_pipeline.providers.ncei import normalize_response as normalize_ncei  # noqa: E402
from weather_pipeline.providers.open_meteo import (  # noqa: E402
    normalize_response as normalize_open_meteo,
)
from weather_pipeline.providers.openweathermap import (  # noqa: E402
    normalize_response as normalize_openweathermap,
)
from weather_pipeline.providers.tomorrow_io import (  # noqa: E402
    normalize_response as normalize_tomorrow_io,
)
from weather_pipeline.providers.weatherbit import (  # noqa: E402
    normalize_response as normalize_weatherbit,
)
from weather_pipeline.storage import read_jsonl, upsert_jsonl  # noqa: E402


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stations = load_stations(PROJECT_ROOT / "config/stations.csv")
        fixtures = PROJECT_ROOT / "tests/fixtures"
        cls.open_meteo_payload = json.loads(
            (fixtures / "open_meteo_forecast.json").read_text(encoding="utf-8")
        )
        cls.ncei_payload = json.loads(
            (fixtures / "ncei_daily_summaries.json").read_text(encoding="utf-8")
        )

    def test_station_configuration_has_expected_unique_stations(self) -> None:
        self.assertEqual(8, len(self.stations))
        self.assertEqual(8, len({station.station_id for station in self.stations}))
        self.assertTrue(all(station.active for station in self.stations))

    def test_open_meteo_selects_next_local_calendar_day(self) -> None:
        record = normalize_open_meteo(
            self.stations[0],
            self.open_meteo_payload,
            retrieved_at=datetime(2026, 10, 25, 12, 17, tzinfo=timezone.utc),
            source_url="https://example.test",
        )
        self.assertEqual("2026-10-26", record["target_date_local"])
        self.assertEqual(70.7, record["forecast_high_f"])
        self.assertEqual(39.2, record["forecast_low_f"])

    def test_ncei_normalizes_daily_extremes(self) -> None:
        records = normalize_ncei(
            self.stations,
            self.ncei_payload,
            retrieved_at=datetime(2026, 10, 25, 12, 17, tzinfo=timezone.utc),
        )
        self.assertEqual(2, len(records))
        self.assertEqual(67.0, records[0]["actual_high_f"])
        self.assertEqual(50.0, records[0]["actual_low_f"])
        self.assertTrue(records[0]["complete"])

    def test_openweathermap_aggregates_target_local_day(self) -> None:
        payload = {
            "list": [
                {
                    "dt": int((datetime(2026, 10, 26, 3, tzinfo=timezone.utc)
                               + timedelta(hours=3 * index)).timestamp()),
                    "main": {"temp": temperature, "temp_max": 100, "temp_min": -10},
                }
                for index, temperature in enumerate([99, 48, 53, 60, 65, 71, 67, 59, 51, -20])
            ]
        }
        record = normalize_openweathermap(
            self.stations[0],
            payload,
            retrieved_at=datetime(2026, 10, 25, 12, 17, tzinfo=timezone.utc),
            source_url="https://example.test",
        )
        self.assertEqual(71.0, record["forecast_high_f"])
        self.assertEqual(48.0, record["forecast_low_f"])
        self.assertEqual("3h", record["source_resolution"])

    def test_noaa_observations_map_to_kalshi_airports_not_legacy_stations(self) -> None:
        payload = [
            {"STATION": station_id, "DATE": "2026-10-25", "TMAX": "70", "TMIN": "50"}
            for station_id in ("USW00014819", "USW00003927", "USW00094846", "USW00013960")
        ]
        records = normalize_ncei(
            self.stations, payload,
            retrieved_at=datetime(2026, 10, 26, 12, 17, tzinfo=timezone.utc),
        )
        self.assertEqual({"chicago_midway", "dallas_fort_worth"}, {r["station_id"] for r in records})
        self.assertEqual(2, len(records))

    def test_weatherbit_selects_midnight_to_midnight_values(self) -> None:
        payload = {
            "data": [
                {"valid_date": "2026-10-25", "max_temp": 65, "min_temp": 42},
                {"valid_date": "2026-10-26", "max_temp": 72, "min_temp": 49},
            ]
        }
        record = normalize_weatherbit(
            self.stations[0],
            payload,
            retrieved_at=datetime(2026, 10, 25, 12, 17, tzinfo=timezone.utc),
            source_url="https://example.test",
        )
        self.assertEqual(72.0, record["forecast_high_f"])
        self.assertEqual(49.0, record["forecast_low_f"])

    def test_tomorrow_io_aggregates_hourly_target_local_day(self) -> None:
        payload = {
            "timelines": {
                "hourly": [
                    {
                        "time": (datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
                                 + timedelta(hours=index)).isoformat(),
                        "values": {"temperature": temperature},
                    }
                    for index, temperature in enumerate([47, 73] + [60] * 22 + [-20])
                ]
            }
        }
        record = normalize_tomorrow_io(
            self.stations[0],
            payload,
            retrieved_at=datetime(2026, 10, 25, 12, 17, tzinfo=timezone.utc),
            source_url="https://example.test",
        )
        self.assertEqual(73.0, record["forecast_high_f"])
        self.assertEqual(47.0, record["forecast_low_f"])
        self.assertEqual("1h", record["source_resolution"])

    def test_jsonl_upsert_is_idempotent(self) -> None:
        row = {"provider": "test", "station_id": "abc", "value": 1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            first = upsert_jsonl(
                path,
                [row],
                key_fields=("provider", "station_id"),
            )
            second = upsert_jsonl(
                path,
                [row],
                key_fields=("provider", "station_id"),
            )
            self.assertEqual(1, first)
            self.assertEqual(0, second)
            self.assertEqual([row], read_jsonl(path))

    def test_jsonl_upsert_ignores_retrieval_time_only_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            original = {
                "provider": "test",
                "station_id": "abc",
                "value": 1,
                "retrieved_at_utc": "first",
            }
            updated = {**original, "retrieved_at_utc": "second"}
            upsert_jsonl(path, [original], key_fields=("provider", "station_id"))
            changes = upsert_jsonl(
                path,
                [updated],
                key_fields=("provider", "station_id"),
                replace=True,
                ignore_compare_fields=frozenset({"retrieved_at_utc"}),
            )
            self.assertEqual(0, changes)
            self.assertEqual("first", read_jsonl(path)[0]["retrieved_at_utc"])


if __name__ == "__main__":
    unittest.main()
