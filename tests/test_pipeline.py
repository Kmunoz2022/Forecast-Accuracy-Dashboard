from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from weather_pipeline.config import load_stations  # noqa: E402
from weather_pipeline.providers.ncei import normalize_response as normalize_ncei  # noqa: E402
from weather_pipeline.providers.open_meteo import (  # noqa: E402
    normalize_response as normalize_open_meteo,
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
