from __future__ import annotations

import io
import json
import sys
import tempfile
import traceback
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import collect_forecasts
import collect_observations
from weather_pipeline.config import load_stations
from weather_pipeline.http import RequestError, get_json
from weather_pipeline.providers.ncei import build_url
from weather_pipeline.providers.nws import fetch_forecast, normalize_response
from weather_pipeline.storage import read_jsonl
from weather_pipeline.temperatures import sampled_extrema


STATIONS = load_stations(PROJECT_ROOT / "config/stations.csv")
AS_OF = datetime(2026, 10, 25, 12, 17, tzinfo=timezone.utc)


def nws_payload() -> dict:
    return {"properties": {"updateTime": "2026-10-25T12:00:00Z", "periods": [
        {
            "startTime": (datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
                          + timedelta(hours=index)).isoformat(),
            "temperature": temperature,
            "temperatureUnit": "C",
        }
        for index, temperature in enumerate([0, 20] + [10] * 22 + [-100])
    ]}}


class TemperatureTests(unittest.TestCase):
    def test_dst_days_require_23_or_25_distinct_utc_hours(self):
        for as_of, start, count in [
            (datetime(2026, 3, 7, 12, tzinfo=timezone.utc),
             datetime(2026, 3, 8, 5, tzinfo=timezone.utc), 23),
            (datetime(2026, 10, 31, 12, tzinfo=timezone.utc),
             datetime(2026, 11, 1, 4, tzinfo=timezone.utc), 25),
        ]:
            with self.subTest(count=count):
                samples = [(start + timedelta(hours=i), i) for i in range(count)]
                result = sampled_extrema(STATIONS[0], samples, retrieved_at=as_of, step_hours=1)
                self.assertEqual(count, result["sample_count"])
                self.assertEqual(count - 1, result["forecast_high_f"])
                with self.assertRaisesRegex(ValueError, "Incomplete"):
                    sampled_extrema(STATIONS[0], samples[:-1] + samples[:1],
                                    retrieved_at=as_of, step_hours=1)

    def test_missing_and_nonfinite_temperatures_are_rejected(self):
        for bad_value in (None, float("nan"), float("inf")):
            with self.subTest(value=bad_value):
                payload = nws_payload()
                payload["properties"]["periods"][12]["temperature"] = bad_value
                with self.assertRaises(ValueError):
                    normalize_response(STATIONS[0], payload, retrieved_at=AS_OF, source_url="test")

    def test_nws_discovers_hourly_endpoint_converts_units_and_excludes_next_day(self):
        url = "https://api.weather.gov/gridpoints/OKX/33,37/forecast/hourly"
        fetch = Mock(side_effect=[{"properties": {"forecastHourly": url}}, nws_payload()])
        record, _, source = fetch_forecast(STATIONS[0], retrieved_at=AS_OF, fetch_json=fetch)
        self.assertEqual(2, fetch.call_count)
        self.assertEqual(url, source)
        self.assertEqual("2026-10-26", record["target_date_local"])
        self.assertEqual(32, record["forecast_low_f"])
        self.assertEqual(68, record["forecast_high_f"])
        self.assertEqual(24, record["sample_count"])
        self.assertEqual("NOAA National Weather Service", record["attribution"])

    def test_nws_missing_endpoint_does_not_fetch_again(self):
        fetch = Mock(return_value={"properties": {}})
        with self.assertRaisesRegex(ValueError, "no hourly forecast"):
            fetch_forecast(STATIONS[0], retrieved_at=AS_OF, fetch_json=fetch)
        self.assertEqual(1, fetch.call_count)

    def test_ncei_requests_only_extremes_for_all_stations_in_one_call(self):
        url = build_url(STATIONS, date(2026, 10, 1), date(2026, 10, 14))
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(["TMAX,TMIN"], query["dataTypes"])
        self.assertEqual(8, len(query["stations"][0].split(",")))


class HttpTests(unittest.TestCase):
    def test_auth_failure_is_not_retried_and_traceback_redacts_key(self):
        private = "https://example.test?appid=secret"
        error = HTTPError(private, 401, "secret", {}, io.BytesIO(b"secret"))
        with patch("weather_pipeline.http.urlopen", side_effect=error) as request:
            try:
                get_json(private, redacted_url="https://example.test")
                self.fail("Expected RequestError")
            except RequestError as exc:
                self.assertEqual(401, exc.status_code)
                self.assertIn("1 attempt(s)", str(exc))
                self.assertNotIn("secret", traceback.format_exc())
            self.assertEqual(1, request.call_count)

    def test_retry_after_is_respected(self):
        error = HTTPError("https://example.test", 429, "limited", {"Retry-After": "7"}, None)
        with patch("weather_pipeline.http.urlopen",
                   side_effect=[error, io.BytesIO(b'{"ok": true}')]) as request, \
                patch("weather_pipeline.http.time.sleep") as sleep:
            self.assertEqual({"ok": True}, get_json("https://example.test"))
            sleep.assert_called_once_with(7)
            self.assertEqual(2, request.call_count)

    def test_long_quota_delay_stops_without_retrying_early(self):
        error = HTTPError("https://example.test", 429, "limited", {"Retry-After": "3600"}, None)
        with patch("weather_pipeline.http.urlopen", side_effect=error) as request, \
                patch("weather_pipeline.http.time.sleep") as sleep:
            with self.assertRaises(RequestError):
                get_json("https://example.test")
            self.assertEqual(1, request.call_count)
            sleep.assert_not_called()


class CollectorTests(unittest.TestCase):
    def run_forecasts(self, directory, providers, *options):
        with patch.object(sys, "argv", ["collect_forecasts", "--data-dir", directory,
                                        "--as-of", AS_OF.isoformat(), *options]), \
                patch.object(collect_forecasts, "configured_providers", return_value=providers), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO):
            return collect_forecasts.main()

    @staticmethod
    def successful_fetch(station):
        record = {
            "provider": "test", "station_id": station.station_id,
            "target_date_local": "2026-10-26", "snapshot_date_utc": "2026-10-25",
            "forecast_high_f": 70, "forecast_low_f": 50,
        }
        return record, {"extra": "raw payload"}, "https://example.test"

    def test_lean_default_and_repeat_run_makes_no_requests(self):
        fetch = Mock(side_effect=self.successful_fetch)
        providers = [collect_forecasts.EnabledProvider("test", fetch)]
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(0, self.run_forecasts(directory, providers))
            self.assertEqual(8, fetch.call_count)
            path = Path(directory) / "forecasts/2026/10/2026-10-25.jsonl"
            self.assertEqual(8, len(read_jsonl(path)))
            self.assertFalse((Path(directory) / "raw").exists())
            fetch.reset_mock()
            self.assertEqual(0, self.run_forecasts(directory, providers))
            fetch.assert_not_called()
            self.assertEqual(0, self.run_forecasts(directory, providers, "--refresh", "--save-raw"))
            self.assertEqual(8, len(list((Path(directory) / "raw").rglob("*.json"))))

    def test_provider_quota_failure_preserves_other_provider_results(self):
        failed = Mock(side_effect=RequestError("quota exhausted", status_code=429))
        success = Mock(side_effect=self.successful_fetch)
        providers = [collect_forecasts.EnabledProvider("failed", failed),
                     collect_forecasts.EnabledProvider("test", success)]
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(1, self.run_forecasts(directory, providers))
            self.assertEqual(1, failed.call_count)
            self.assertEqual(8, success.call_count)
            self.assertEqual(8, len(read_jsonl(Path(directory) / "forecasts/2026/10/2026-10-25.jsonl")))

    def test_no_active_stations_makes_no_observation_request(self):
        with patch.object(sys, "argv", ["collect_observations"]), \
                patch.object(collect_observations, "load_stations", return_value=[]), \
                patch.object(collect_observations, "fetch_observations") as fetch, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, collect_observations.main())
            fetch.assert_not_called()

    def test_observations_save_only_normalized_rows_by_default(self):
        row = {"provider": "noaa_ncei_daily_summaries", "station_id": "nyc_central_park",
               "target_date_local": "2026-10-24", "actual_high_f": 70, "actual_low_f": 50}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sys, "argv", ["collect_observations", "--data-dir", directory]), \
                patch.object(collect_observations, "fetch_observations",
                             return_value=([row], [{"raw": "value"}], "test")), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, collect_observations.main())
            self.assertFalse((Path(directory) / "raw").exists())
            self.assertEqual([row], read_jsonl(Path(directory) / "observations/2026/10/2026-10-24.jsonl"))


if __name__ == "__main__":
    unittest.main()
