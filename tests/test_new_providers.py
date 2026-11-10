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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import collect_forecasts
from export_private_forecasts import export_forecasts
from weather_pipeline.config import load_stations
from weather_pipeline.http import RequestError
from weather_pipeline.providers import pirate_weather, visual_crossing
from weather_pipeline.storage import read_jsonl, upsert_jsonl, write_json
from weather_pipeline.windows import local_day, standard_day

STATION = load_stations(ROOT / "config/stations.csv")[0]
AS_OF = datetime(2026, 10, 25, 12, 17, tzinfo=timezone.utc)
SECRET = "test-provider-secret-never-save-this"


def payload_for(adapter, start, count):
    points = [(start - timedelta(hours=1), -100)] + [
        (start + timedelta(hours=i), 40 + i) for i in range(count)
    ] + [(start + timedelta(hours=count), 100)]
    if adapter is pirate_weather:
        return {
            "flags": {"units": "us"},
            "hourly": {"data": [{"time": int(t.timestamp()), "temperature": v} for t, v in points]},
            # These daytime/overnight values must never be used for full-day highs/lows.
            "daily": {"data": [{"temperatureHigh": 999, "temperatureLow": -999}]},
        }
    return {"days": [{"hours": [
        {"datetimeEpoch": int(t.timestamp()), "temp": v, "source": "fcst"} for t, v in points
    ]}]}


def points_for(adapter, payload):
    return payload["hourly"]["data"] if adapter is pirate_weather else payload["days"][0]["hours"]


class ForecastAdapterTests(unittest.TestCase):
    def test_daily_windows_cover_both_dst_transitions(self):
        for as_of, target, count in [
            (datetime(2026, 3, 7, 12, tzinfo=timezone.utc), date(2026, 3, 8), 23),
            (datetime(2026, 10, 31, 12, tzinfo=timezone.utc), date(2026, 11, 1), 25),
        ]:
            window = local_day(target, STATION.timezone)
            for adapter in (pirate_weather, visual_crossing):
                with self.subTest(adapter=adapter.__name__, count=count):
                    row = adapter.normalize_response(
                        STATION, payload_for(adapter, window.start, count),
                        retrieved_at=as_of, source_url="test",
                    )
                    self.assertEqual((40, 40 + count - 1), (row["forecast_low_f"], row["forecast_high_f"]))
                    self.assertEqual(count, row["sample_count"])
                    self.assertEqual(target.isoformat(), row["target_date_local"])
                    self.assertEqual("local_midnight_to_midnight", row["daily_boundary"])

    def test_explicit_market_date_and_standard_time_window(self):
        window = standard_day(date(2026, 10, 27), STATION.timezone)
        for adapter in (pirate_weather, visual_crossing):
            with self.subTest(adapter=adapter.__name__):
                row = adapter.normalize_response(
                    STATION, payload_for(adapter, window.start, 24),
                    retrieved_at=AS_OF, source_url="test", window=window,
                )
                self.assertEqual((40, 63), (row["forecast_low_f"], row["forecast_high_f"]))
                self.assertEqual(24, row["sample_count"])
                for field, value in window.metadata().items():
                    self.assertEqual(value, row[field])

    def test_incomplete_invalid_and_conflicting_hours_are_rejected(self):
        window = standard_day(date(2026, 10, 26), STATION.timezone)
        for adapter in (pirate_weather, visual_crossing):
            for defect in ("missing", "null", "nan", "infinite", "conflict"):
                with self.subTest(adapter=adapter.__name__, defect=defect):
                    payload = payload_for(adapter, window.start, 24)
                    points = points_for(adapter, payload)
                    field = "temperature" if adapter is pirate_weather else "temp"
                    if defect == "missing":
                        del points[5]
                    elif defect == "conflict":
                        points.append({**points[5], field: 1})
                    else:
                        points[5][field] = {"null": None, "nan": float("nan"), "infinite": float("inf")}[defect]
                    with self.assertRaises(ValueError):
                        adapter.normalize_response(STATION, payload, retrieved_at=AS_OF,
                                                   source_url="test", window=window)

    def test_visual_crossing_accepts_only_actual_forecast_hours(self):
        window = standard_day(date(2026, 10, 26), STATION.timezone)
        for source in ("obs", "histfcst", "stats", "comb", None):
            with self.subTest(source=source):
                payload = payload_for(visual_crossing, window.start, 24)
                points_for(visual_crossing, payload)[5]["source"] = source
                with self.assertRaisesRegex(ValueError, "non-forecast"):
                    visual_crossing.normalize_response(STATION, payload, retrieved_at=AS_OF,
                                                        source_url="test", window=window)

    def test_pirate_missing_sentinel_and_wrong_units_are_rejected(self):
        window = standard_day(date(2026, 10, 26), STATION.timezone)
        payload = payload_for(pirate_weather, window.start, 24)
        payload["hourly"]["data"][5]["temperature"] = -999
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            pirate_weather.normalize_response(STATION, payload, retrieved_at=AS_OF,
                                               source_url="test", window=window)
        payload["flags"]["units"] = "si"
        with self.assertRaisesRegex(ValueError, "Fahrenheit"):
            pirate_weather.normalize_response(STATION, payload, retrieved_at=AS_OF,
                                               source_url="test", window=window)

    def test_past_or_started_market_window_makes_no_request(self):
        for adapter in (pirate_weather, visual_crossing):
            with self.subTest(adapter=adapter.__name__):
                fetch = Mock()
                with self.assertRaisesRegex(ValueError, "has not started"):
                    adapter.fetch_forecast(
                        STATION, retrieved_at=AS_OF, api_key=SECRET, fetch_json=fetch,
                        window=standard_day(AS_OF.date(), STATION.timezone),
                    )
                fetch.assert_not_called()

    def test_pirate_uses_live_hourly_endpoint_without_historical_timestamp(self):
        private, public = pirate_weather.build_urls(STATION, SECRET)
        parsed = urlsplit(private)
        self.assertEqual("api.pirateweather.net", parsed.netloc)
        self.assertEqual(f"/forecast/{SECRET}/{STATION.latitude},{STATION.longitude}", parsed.path)
        query = parse_qs(parsed.query)
        self.assertEqual(["hourly"], query["extend"])
        self.assertEqual(["us"], query["units"])
        self.assertIn("daily", query["exclude"][0])
        self.assertNotIn(SECRET, public)

    def test_visual_crossing_requests_only_target_dates_and_forecast_fields(self):
        for window, end in [(None, "2026-10-26"),
                            (standard_day(date(2026, 10, 26), STATION.timezone), "2026-10-27")]:
            with self.subTest(window=window):
                private, public = visual_crossing.build_urls(
                    STATION, SECRET, retrieved_at=AS_OF, window=window,
                )
                parsed = urlsplit(private)
                self.assertTrue(parsed.path.endswith(f"/2026-10-26/{end}"))
                query = parse_qs(parsed.query)
                self.assertEqual(["hours,fcst"], query["include"])
                self.assertEqual(["us"], query["unitGroup"])
                self.assertEqual([SECRET], query["key"])
                self.assertNotIn("forecastBasisDate", query)
                self.assertNotIn("forecastBasisDay", query)
                self.assertNotIn("key", parse_qs(urlsplit(public).query))
                self.assertNotIn(SECRET, public)

    def test_fetches_normalize_and_return_only_redacted_urls(self):
        window = standard_day(date(2026, 10, 26), STATION.timezone)
        for adapter in (pirate_weather, visual_crossing):
            with self.subTest(adapter=adapter.__name__):
                payload = payload_for(adapter, window.start, 24)
                fetch = Mock(return_value=payload)
                row, raw, public = adapter.fetch_forecast(
                    STATION, retrieved_at=AS_OF, api_key=SECRET, fetch_json=fetch, window=window,
                )
                self.assertEqual(1, fetch.call_count)
                self.assertEqual(payload, raw)
                self.assertIn(SECRET, fetch.call_args.args[0])
                self.assertEqual(public, fetch.call_args.kwargs["redacted_url"])
                self.assertNotIn(SECRET, json.dumps(row))
                self.assertNotIn(SECRET, public)

    def test_authentication_errors_and_tracebacks_do_not_expose_key(self):
        for adapter in (pirate_weather, visual_crossing):
            with self.subTest(adapter=adapter.__name__):
                error = HTTPError(f"https://example.test/{SECRET}", 401, SECRET, {}, None)
                with patch("weather_pipeline.http.urlopen", side_effect=error) as request:
                    try:
                        adapter.fetch_forecast(STATION, retrieved_at=AS_OF, api_key=SECRET)
                        self.fail("Expected RequestError")
                    except RequestError as exc:
                        self.assertEqual(401, exc.status_code)
                        self.assertNotIn(SECRET, traceback.format_exc())
                    self.assertEqual(1, request.call_count)

    def test_non_object_responses_are_rejected(self):
        for adapter in (pirate_weather, visual_crossing):
            with self.subTest(adapter=adapter.__name__):
                with self.assertRaisesRegex(ValueError, "non-object"):
                    adapter.fetch_forecast(STATION, retrieved_at=AS_OF, api_key=SECRET,
                                           fetch_json=Mock(return_value=[]))


class ProviderConfigurationTests(unittest.TestCase):
    def test_secrets_enable_both_providers_in_both_workflows(self):
        credentials = {"PIRATE_WEATHER_API_KEY": "pirate-secret", "VISUALCROSSING_API_KEY": SECRET}
        for window in (None, standard_day(date(2026, 10, 26), STATION.timezone)):
            with self.subTest(window=window), patch.dict("os.environ", credentials, clear=True), \
                    patch.object(collect_forecasts, "fetch_pirate_weather") as pirate, \
                    patch.object(collect_forecasts, "fetch_visual_crossing") as visual:
                providers = {p.slug: p for p in collect_forecasts.configured_providers(AS_OF, window, quiet=True)}
                providers["pirate_weather"].fetch(STATION)
                providers["visual_crossing"].fetch(STATION)
                pirate.assert_called_once_with(STATION, retrieved_at=AS_OF, api_key="pirate-secret", window=window)
                visual.assert_called_once_with(STATION, retrieved_at=AS_OF, api_key=SECRET, window=window)
                self.assertEqual(SECRET, providers["visual_crossing"].archive_key)
                self.assertNotIn(SECRET, repr(providers))

    def test_missing_keys_skip_optional_providers(self):
        with patch.dict("os.environ", {}, clear=True):
            slugs = [p.slug for p in collect_forecasts.configured_providers(AS_OF, quiet=True)]
        self.assertNotIn("pirate_weather", slugs)
        self.assertNotIn("visual_crossing", slugs)


class EncryptedArchiveTests(unittest.TestCase):
    def test_roundtrip_is_idempotent_and_contains_no_plaintext(self):
        row = {"provider": "visual_crossing", "station_id": "test", "forecast_high_f": 71.25}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forecast.jsonl.enc"
            self.assertEqual(1, upsert_jsonl(path, [row], key_fields=("station_id",), encryption_key=SECRET))
            original = path.read_bytes()
            self.assertNotIn(b"forecast_high_f", original)
            self.assertNotIn(SECRET.encode(), original)
            self.assertEqual([row], read_jsonl(path, encryption_key=SECRET))
            self.assertEqual(0, upsert_jsonl(path, [row], key_fields=("station_id",), encryption_key=SECRET))
            self.assertEqual(original, path.read_bytes())
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_wrong_key_and_tampering_do_not_overwrite_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forecast.jsonl.enc"
            upsert_jsonl(path, [{"id": 1}], key_fields=("id",), encryption_key=SECRET)
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Cannot decrypt"):
                upsert_jsonl(path, [{"id": 2}], key_fields=("id",), encryption_key="wrong-key")
            self.assertEqual(original, path.read_bytes())
            corrupt = bytearray(original)
            corrupt[-20] ^= 1
            path.write_bytes(corrupt)
            with self.assertRaisesRegex(ValueError, "Cannot decrypt"):
                read_jsonl(path, encryption_key=SECRET)

    def test_encrypted_storage_requires_key_and_correct_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename, secret in (("forecast.json", SECRET), ("forecast.json.enc", None)):
                with self.subTest(filename=filename):
                    path = Path(directory) / filename
                    with self.assertRaises(ValueError):
                        write_json(path, {"private": "data"}, encryption_key=secret)
                    self.assertFalse(path.exists())

    def test_daily_collection_encrypts_visual_crossing_including_optional_raw(self):
        def fetch(station):
            return ({"provider": "visual_crossing", "station_id": station.station_id,
                     "target_date_local": "2026-10-26", "snapshot_date_utc": "2026-10-25",
                     "forecast_high_f": 71.25}, {"private_raw": "weather"}, "test")
        request = Mock(side_effect=fetch)
        provider = collect_forecasts.EnabledProvider("visual_crossing", request, archive_key=SECRET)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(collect_forecasts, "configured_providers", return_value=[provider]), \
                patch.object(collect_forecasts, "load_stations", return_value=[STATION]), \
                patch.object(sys, "argv", ["collect_forecasts", "--data-dir", directory,
                                           "--as-of", AS_OF.isoformat(), "--save-raw"]), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, collect_forecasts.main())
            self.assertEqual(0, collect_forecasts.main())
            self.assertEqual(1, request.call_count)
            files = [p for p in Path(directory).rglob("*") if p.is_file()]
            self.assertEqual(2, len(files))
            self.assertTrue(all(p.suffix == ".enc" for p in files))
            self.assertTrue(all(b"private_raw" not in p.read_bytes() for p in files))
            self.assertTrue(all(b"forecast_high_f" not in p.read_bytes() for p in files))
            path = provider.output_path(Path(directory) / "forecasts/2026/10/2026-10-25.jsonl")
            self.assertEqual(71.25, read_jsonl(path, encryption_key=SECRET)[0]["forecast_high_f"])

    def test_export_preserves_daily_and_market_datasets_without_network(self):
        daily = {"provider": "visual_crossing", "station_id": STATION.station_id,
                 "target_date_local": "2026-10-26", "snapshot_date_utc": "2026-10-25",
                 "forecast_high_f": 71.25}
        market = {**daily, "event_ticker": "KXHIGHNY-26OCT26",
                  "market_open_time_utc": "2026-10-25T14:00:00Z"}
        with tempfile.TemporaryDirectory() as directory, \
                patch("weather_pipeline.http.urlopen") as network:
            root = Path(directory)
            source_files = []
            for category, row in (("forecasts", daily), ("market_forecasts", market)):
                source = root / category / "2026/10/2026-10-25.jsonl.visual_crossing.enc"
                upsert_jsonl(source, [row], key_fields=("provider",), encryption_key=SECRET)
                source_files.append(source)
            original = [p.read_bytes() for p in source_files]
            self.assertEqual((2, []), export_forecasts(root, SECRET))
            self.assertEqual((0, []), export_forecasts(root, SECRET))
            network.assert_not_called()
            for category, row in (("forecasts", daily), ("market_forecasts", market)):
                output = root / "private/visual_crossing" / category / "2026/10/2026-10-25.jsonl"
                self.assertEqual([row], read_jsonl(output))
                self.assertEqual(0o600, output.stat().st_mode & 0o777)
            self.assertEqual(original, [p.read_bytes() for p in source_files])
            changed, errors = export_forecasts(root, "wrong-key")
            self.assertEqual(0, changed)
            self.assertEqual(2, len(errors))
            self.assertEqual(original, [p.read_bytes() for p in source_files])

    def test_archive_failure_preserves_other_provider_success(self):
        def fetch(station):
            return ({"provider": "pirate_weather", "station_id": station.station_id,
                     "target_date_local": "2026-10-26", "snapshot_date_utc": "2026-10-25"}, {}, "test")
        private_fetch = Mock()
        providers = [collect_forecasts.EnabledProvider("visual_crossing", private_fetch, archive_key="wrong-key"),
                     collect_forecasts.EnabledProvider("pirate_weather", fetch)]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(collect_forecasts, "configured_providers", return_value=providers), \
                patch.object(collect_forecasts, "load_stations", return_value=[STATION]), \
                patch.object(sys, "argv", ["collect_forecasts", "--data-dir", directory,
                                           "--as-of", AS_OF.isoformat()]), \
                patch("sys.stdout", new_callable=io.StringIO), \
                patch("sys.stderr", new_callable=io.StringIO):
            public = Path(directory) / "forecasts/2026/10/2026-10-25.jsonl"
            encrypted = providers[0].output_path(public)
            upsert_jsonl(encrypted, [{"id": 1}], key_fields=("id",), encryption_key=SECRET)
            original = encrypted.read_bytes()
            self.assertEqual(1, collect_forecasts.main())
            private_fetch.assert_not_called()
            self.assertEqual(original, encrypted.read_bytes())
            self.assertEqual("pirate_weather", read_jsonl(public)[0]["provider"])


if __name__ == "__main__":
    unittest.main()
