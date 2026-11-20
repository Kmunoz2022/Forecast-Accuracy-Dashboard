from __future__ import annotations

import base64
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import collect_forecasts
from create_archive_key import create
from export_private_forecasts import export_forecasts
from migrate_archives import plan_migration, read_key_file
from weather_pipeline.archive import load_category, read_archive
from weather_pipeline.encryption import MAGIC, MAGIC_V2, _cipher, decrypt, encrypt, validate_key
from weather_pipeline.storage import read_jsonl, upsert_jsonl, write_json
from test_live_reporting import STATION, NOW, contract, forecast
from collect_market_forecasts import save_contracts
from weather_pipeline.live_report import build_reports

KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()
NEXT_KEY = base64.urlsafe_b64encode(bytes(range(32, 64))).decode()
LEGACY_API_KEY = "synthetic-old-api-key"
FIELDS = ("provider", "station_id", "target_date_local", "snapshot_date_utc")


def legacy_bytes(text):
    # Fixture for archives written by the previous released implementation.
    salt = bytes(range(16))
    return MAGIC + salt + _cipher(LEGACY_API_KEY, salt).encrypt(text.encode())


class ArchiveMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plaintext(self, *providers):
        path = self.root / "forecasts/2026/11/2026-11-09.jsonl"
        rows = [forecast(provider=p, kind="daily") for p in providers]
        upsert_jsonl(path, rows, key_fields=FIELDS)
        return path, rows

    def legacy(self):
        path = self.root / "forecasts/2026/11/2026-11-09.jsonl.visual_crossing.enc"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = forecast(provider="visual_crossing", kind="daily")
        path.write_bytes(legacy_bytes(json.dumps(row) + "\n"))
        return path, row

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_migrates_mixed_forecasts_raw_and_legacy_without_touching_excluded_providers(self):
        plain, rows = self.plaintext("open_meteo", "openweathermap", "pirate_weather", "weatherbit", "tomorrow_io")
        old, visual = self.legacy()
        raw = self.root / "raw/openweathermap/2026/11/station.json"
        write_json(raw, {"private_canary": "restricted weather payload"})
        plan = plan_migration(self.root, KEY, legacy_key=LEGACY_API_KEY)
        self.assertEqual(2, plan.forecast_rows)
        self.assertTrue(raw.exists())  # Planning is read-only.
        plan.apply()
        self.assertEqual({"open_meteo", "weatherbit", "tomorrow_io"}, {r["provider"] for r in read_jsonl(plain)})
        for provider in ("openweathermap", "pirate_weather"):
            path = plain.with_name(plain.name + f".{provider}.enc")
            self.assertEqual([r for r in rows if r["provider"] == provider], read_archive(path, key=KEY))
            self.assertNotIn(b"forecast_high_f", path.read_bytes())
        self.assertEqual([visual], read_archive(old, key=KEY))
        self.assertTrue(old.read_bytes().startswith(MAGIC_V2))
        self.assertFalse(raw.exists())
        encrypted_raw = raw.with_name(raw.name + ".openweathermap.enc")
        self.assertNotIn(b"private_canary", encrypted_raw.read_bytes())
        self.assertEqual({"private_canary": "restricted weather payload"}, json.loads(decrypt(encrypted_raw.read_bytes(), KEY)))
        before = self.snapshot()
        again = plan_migration(self.root, KEY)  # The legacy API key is no longer needed.
        self.assertFalse(again.writes)
        again.apply()
        self.assertEqual(before, self.snapshot())

    def test_original_api_key_is_needed_only_for_legacy_reads_and_migration(self):
        path, row = self.legacy()
        self.assertEqual([row], read_archive(path, legacy_key=LEGACY_API_KEY))
        with patch.dict(os.environ, {"DATA_ENCRYPTION_KEY": KEY}, clear=True):
            rows, problems = load_category(self.root, "forecasts")
        self.assertEqual([], rows)
        self.assertEqual("decryption_key_unavailable", problems[0]["reason"])
        plan_migration(self.root, KEY, legacy_key=LEGACY_API_KEY).apply()
        with patch.dict(os.environ, {"DATA_ENCRYPTION_KEY": KEY, "VISUALCROSSING_API_KEY": "rotated-api-key"}, clear=True):
            self.assertEqual(([row], []), load_category(self.root, "forecasts"))

    def test_missing_or_wrong_legacy_key_leaves_every_file_unchanged(self):
        self.plaintext("pirate_weather", "openweathermap")
        self.legacy()
        before = self.snapshot()
        for old_key in (None, "wrong-key"):
            with self.subTest(old_key=old_key), self.assertRaises(ValueError):
                plan_migration(self.root, KEY, legacy_key=old_key)
            self.assertEqual(before, self.snapshot())

    def test_dedicated_key_rotation_requires_previous_key_and_is_verified(self):
        _, rows = self.plaintext("pirate_weather")
        plan_migration(self.root, KEY).apply()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            plan_migration(self.root, NEXT_KEY)
        self.assertEqual(before, self.snapshot())
        plan_migration(self.root, NEXT_KEY, previous_key=KEY).apply()
        self.assertEqual((rows, []), load_category(self.root, "forecasts", key=NEXT_KEY))
        self.assertEqual("archive_unreadable", load_category(self.root, "forecasts", key=KEY)[1][0]["reason"])

    def test_repeated_partial_migration_preserves_original_snapshot(self):
        plain, rows = self.plaintext("pirate_weather")
        destination = plain.with_name(plain.name + ".pirate_weather.enc")
        upsert_jsonl(destination, rows, key_fields=FIELDS, encryption_key=KEY)
        original = destination.read_bytes()
        plan_migration(self.root, KEY).apply()
        self.assertFalse(plain.exists())
        self.assertEqual(original, destination.read_bytes())
        self.assertEqual(rows, read_archive(destination, key=KEY))

    def test_conflicting_snapshots_abort_without_replacing_originals(self):
        plain, rows = self.plaintext("pirate_weather")
        destination = plain.with_name(plain.name + ".pirate_weather.enc")
        upsert_jsonl(destination, [{**rows[0], "forecast_high_f": 999}], key_fields=FIELDS, encryption_key=KEY)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Conflicting original snapshots"):
            plan_migration(self.root, KEY)
        self.assertEqual(before, self.snapshot())

    def test_modified_source_and_failed_write_never_delete_plaintext(self):
        plain, _ = self.plaintext("pirate_weather")
        plan = plan_migration(self.root, KEY)
        original = plain.read_bytes()
        plain.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            plan.apply()
        self.assertFalse(list(self.root.rglob("*.enc")))
        plan = plan_migration(self.root, KEY)
        with patch.object(Path, "replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                plan.apply()
        self.assertEqual(original + b"\n", plain.read_bytes())
        self.assertFalse(list(self.root.rglob("*.migration.tmp")))

    def test_migration_rejects_providers_without_retention_approval(self):
        self.plaintext("weatherbit", "tomorrow_io")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            plan_migration(self.root, KEY, providers={"weatherbit"})
        self.assertEqual(before, self.snapshot())

    def test_append_to_legacy_archive_reencrypts_without_losing_existing_forecasts(self):
        path, row = self.legacy()
        addition = {**row, "station_id": "another_station"}
        upsert_jsonl(path, [addition], key_fields=FIELDS, encryption_key=KEY, legacy_encryption_key=LEGACY_API_KEY)
        self.assertTrue(path.read_bytes().startswith(MAGIC_V2))
        self.assertEqual(2, len(read_archive(path, key=KEY)))

    def test_collectors_skip_existing_plaintext_and_keep_capture_timestamps(self):
        plain, rows = self.plaintext("pirate_weather")
        request = Mock(side_effect=AssertionError("Original forecasts must not be fetched again"))
        provider = collect_forecasts.EnabledProvider("pirate_weather", request, archive_key=KEY)
        with patch.object(sys, "argv", ["collect_forecasts", "--data-dir", str(self.root),
                                        "--as-of", "2026-11-09T12:17:00Z"]), \
                patch.object(collect_forecasts, "configured_providers", return_value=[provider]), \
                patch.object(collect_forecasts, "load_stations", return_value=[STATION]), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, collect_forecasts.main())
        request.assert_not_called()
        plan_migration(self.root, KEY).apply()
        self.assertEqual(rows, read_archive(plain.with_name(plain.name + ".pirate_weather.enc"), key=KEY))

    def test_reporting_and_export_support_each_encrypted_provider_without_releasing_values(self):
        _, rows = self.plaintext("openweathermap", "pirate_weather", "visual_crossing")
        save_contracts(self.root, [contract(r) for r in rows])
        plan_migration(self.root, KEY).apply()
        health, accuracy = build_reports(self.root, [STATION], NOW, key=KEY)
        self.assertEqual("complete", health["status"])
        self.assertNotIn("forecast_high_f", json.dumps([health, accuracy]))
        with patch.dict(os.environ, {}, clear=True):
            health, accuracy = build_reports(self.root, [STATION], NOW)
        self.assertEqual(["openweathermap", "pirate_weather", "visual_crossing"], accuracy["excluded_providers"])
        self.assertEqual((3, []), export_forecasts(self.root, KEY))
        for provider in accuracy["excluded_providers"]:
            path = next((self.root / "private" / provider).rglob("*.jsonl"))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))


class KeyConfigurationTests(unittest.TestCase):
    def test_new_key_file_is_private_and_never_overwrites_existing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            create(path)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            key = read_key_file(path)
            self.assertEqual(key, validate_key(key))
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                create(path)
            self.assertEqual(original, path.read_bytes())

    def test_private_providers_require_archive_key_and_never_use_api_keys_for_new_encryption(self):
        credentials = {"OPENWEATHERMAP_API_KEY": "open-api", "PIRATE_WEATHER_API_KEY": "pirate-api",
                       "VISUALCROSSING_API_KEY": LEGACY_API_KEY}
        now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        with patch.dict(os.environ, credentials, clear=True):
            with self.assertRaisesRegex(ValueError, "DATA_ENCRYPTION_KEY"):
                collect_forecasts.configured_providers(now, quiet=True)
        with patch.dict(os.environ, {**credentials, "DATA_ENCRYPTION_KEY": KEY}, clear=True):
            providers = collect_forecasts.configured_providers(now, quiet=True)
        for provider in providers:
            self.assertEqual(None if provider.slug in {"open_meteo", "nws"} else KEY, provider.archive_key)
        self.assertNotIn(KEY, repr(providers))
        self.assertNotIn(LEGACY_API_KEY, repr(providers))

    def test_disabled_providers_do_not_need_archive_key(self):
        with patch.dict(os.environ, {"WEATHERBIT_API_KEY": "api", "TOMORROW_API_KEYS": "api",
                                     "WEATHERBIT_ARCHIVAL_ALLOWED": "false", "TOMORROW_ARCHIVAL_ALLOWED": "false"}, clear=True):
            providers = collect_forecasts.configured_providers(NOW, quiet=True)
        self.assertEqual({"nws", "open_meteo"}, {p.slug for p in providers})


if __name__ == "__main__":
    unittest.main()
