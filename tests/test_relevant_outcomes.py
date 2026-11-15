from __future__ import annotations

import io
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import collect_observations
from collect_market_forecasts import save_contracts
from refresh_settlements import refresh
from weather_pipeline.config import load_stations
from weather_pipeline.kalshi import normalize_contract
from weather_pipeline.storage import read_jsonl, upsert_jsonl
from test_market_collection import MAPPING, OPEN, SERIES, market

STATIONS = load_stations(ROOT / "config/stations.csv")
NOW = datetime(2026, 11, 15, 18, tzinfo=timezone.utc)


class RelevantOutcomesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def targets(self, rows):
        upsert_jsonl(self.root / "daily_collection/test.jsonl", [
            {"station_id": station_id, "target_date_local": day, "status": "saved"} for station_id, day in rows],
            key_fields=("station_id", "target_date_local"))

    def test_no_saved_forecasts_means_no_observation_requests(self):
        with patch.object(sys, "argv", ["collect_observations", "--data-dir", str(self.root)]), \
                patch.object(collect_observations, "fetch_observations") as fetch, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, collect_observations.main())
            fetch.assert_not_called()

    def test_only_saved_station_days_are_batched_without_filling_gaps(self):
        self.targets([(STATIONS[0].station_id, day) for day in ("2026-10-01", "2026-11-10", "2026-11-11", "2026-11-13")]
                     + [(STATIONS[1].station_id, "2026-11-13")])
        requests = collect_observations.needed_requests(self.root, STATIONS, NOW,
                                                        end_date=date(2026, 11, 14), lookback_days=14)
        self.assertEqual([("2026-10-01", "2026-10-01", 1), ("2026-11-10", "2026-11-11", 1),
                          ("2026-11-13", "2026-11-13", 2)],
                         [(str(start), str(end), len(stations)) for start, end, stations in requests])

    def test_old_complete_days_are_skipped_but_missing_values_and_recent_corrections_retry(self):
        days = ["2026-10-01", "2026-10-02", "2026-11-10"]
        self.targets([(STATIONS[0].station_id, day) for day in days])
        upsert_jsonl(self.root / "observations/test.jsonl", [
            {"station_id": STATIONS[0].station_id, "target_date_local": day,
             "actual_high_f": 70, "actual_low_f": None if day.endswith("02") else 50} for day in days],
            key_fields=("station_id", "target_date_local"))
        requests = collect_observations.needed_requests(self.root, STATIONS, NOW,
                                                        end_date=date(2026, 11, 14), lookback_days=14)
        self.assertEqual([date(2026, 10, 2), date(2026, 11, 10)], [r[0] for r in requests])

    def test_measurement_must_end_locally_before_fetching(self):
        self.targets([(s.station_id, "2026-11-14") for s in STATIONS[:2]])
        now = datetime(2026, 11, 15, 6, tzinfo=timezone.utc)
        requests = collect_observations.needed_requests(self.root, STATIONS, now,
                                                        end_date=date(2026, 11, 14), lookback_days=14)
        self.assertEqual([STATIONS[0]], requests[0][2])  # NYC ended; Los Angeles has not.

    def test_unrelated_response_rows_are_not_saved_and_partial_failure_keeps_success(self):
        self.targets([(STATIONS[0].station_id, day) for day in ("2026-11-10", "2026-11-12")])
        row = {"provider": "noaa_ncei_daily_summaries", "station_id": STATIONS[0].station_id,
               "target_date_local": "2026-11-12", "actual_high_f": 70, "actual_low_f": 50}
        fetch = Mock(side_effect=[ValueError("unavailable"),
                                  ([row, {**row, "target_date_local": "2020-01-01"}], [], "test")])
        with patch.object(sys, "argv", ["collect_observations", "--data-dir", str(self.root), "--as-of", NOW.isoformat()]), \
                patch.object(collect_observations, "fetch_observations", fetch), \
                patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(1, collect_observations.main())
        self.assertEqual([row], read_jsonl(self.root / "observations/2026/11/2026-11-12.jsonl"))
        self.assertFalse((self.root / "observations/2020").exists())

    def test_settlement_refresh_targets_only_relevant_finished_events_even_when_old(self):
        current = market()
        normalized = normalize_contract(MAPPING, current, SERIES, OPEN)
        save_contracts(self.root, [normalized])
        client = Mock()
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, refresh(self.root, [MAPPING], NOW, client=client))
            client.get.assert_not_called()
            self.targets([(STATIONS[0].station_id, "2026-10-26")])
            self.assertEqual(0, refresh(self.root, [MAPPING], OPEN, client=client))
            client.get.assert_not_called()
            settled = {**current, "status": "finalized", "result": "yes", "expiration_value": "76"}
            client.get.return_value = {"event": {"event_ticker": current["event_ticker"], "markets": [settled]}}
            client.series.return_value = SERIES
            self.assertEqual(0, refresh(self.root, [MAPPING], NOW, client=client))
            client.get.assert_called_once_with(f"events/{current['event_ticker']}", with_nested_markets="true")
            saved = read_jsonl(self.root / "markets/2026/10/2026-10-26.jsonl")
            self.assertEqual("76", saved[0]["expiration_value"])
            client.reset_mock()
            self.assertEqual(0, refresh(self.root, [MAPPING], NOW, client=client))
            client.get.assert_not_called()  # Final and beyond correction window.

    def test_wrong_event_response_preserves_known_contracts(self):
        original = normalize_contract(MAPPING, market(), SERIES, OPEN)
        save_contracts(self.root, [original])
        self.targets([(STATIONS[0].station_id, "2026-10-26")])
        client = Mock()
        client.get.return_value = {"event": {"event_ticker": "unrelated"}}
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(1, refresh(self.root, [MAPPING], NOW, client=client))
        self.assertEqual([original], read_jsonl(self.root / "markets/2026/10/2026-10-26.jsonl"))


if __name__ == "__main__":
    unittest.main()
