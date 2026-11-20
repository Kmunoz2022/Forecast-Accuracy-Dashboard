"""Decrypt protected forecasts into ignored local files; makes no API calls."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from weather_pipeline.storage import upsert_jsonl
from weather_pipeline.archive import MIGRATION_PROVIDERS, archive_provider, read_archive
from migrate_archives import read_key_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def export_forecasts(data_dir: Path, key: str | None = None, *, legacy_key=None,
                     providers=MIGRATION_PROVIDERS) -> tuple[int, list[str]]:
    # Always use a gitignored directory; never mix private exports into public JSONL.
    changed = 0
    failures = []
    for category, keys in (
        ("forecasts", ("provider", "station_id", "target_date_local", "snapshot_date_utc")),
        ("market_forecasts", ("provider", "event_ticker", "market_open_time_utc")),
    ):
        for source in sorted((data_dir / category).rglob("*.jsonl.*.enc")):
            provider = archive_provider(source)
            if provider not in providers:
                continue
            try:
                rows = read_archive(source, key=key, legacy_key=legacy_key)
                relative = source.relative_to(data_dir)
                destination = data_dir / "private" / provider
                output = destination / relative.with_name(relative.name.removesuffix(f".{provider}.enc"))
                output.parent.mkdir(parents=True, exist_ok=True)
                # Ensure restrictive permissions before any plaintext is written.
                if not output.exists():
                    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.close(descriptor)
                changed += upsert_jsonl(output, rows, key_fields=keys, replace=True)
                # Forecasts contain licensed data; keep exports private to the local user.
                if output.exists():
                    output.chmod(0o600)
            except Exception:
                failures.append(f"{source.name}: archive unavailable or export failed")
    return changed, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--key-file", type=Path, help="Private archive key file; otherwise use DATA_ENCRYPTION_KEY")
    parser.add_argument("--providers", nargs="+", choices=sorted(MIGRATION_PROVIDERS), default=sorted(MIGRATION_PROVIDERS))
    args = parser.parse_args()
    key = read_key_file(args.key_file) if args.key_file else os.getenv("DATA_ENCRYPTION_KEY")
    changed, failures = export_forecasts(args.data_dir, key, providers=args.providers)
    print(f"Private forecast rows exported: {changed} (under {args.data_dir / 'private'})")
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
