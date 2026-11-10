"""Decrypt saved Visual Crossing forecasts for local analysis; makes no API calls."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from weather_pipeline.storage import read_jsonl, upsert_jsonl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUFFIX = ".visual_crossing.enc"


def export_forecasts(data_dir: Path, api_key: str) -> tuple[int, list[str]]:
    # Always use a gitignored directory; never mix private exports into public JSONL.
    destination = data_dir / "private" / "visual_crossing"
    changed = 0
    failures = []
    for category, keys in (
        ("forecasts", ("provider", "station_id", "target_date_local", "snapshot_date_utc")),
        ("market_forecasts", ("provider", "event_ticker", "market_open_time_utc")),
    ):
        for source in sorted((data_dir / category).rglob(f"*.jsonl{SUFFIX}")):
            try:
                rows = read_jsonl(source, encryption_key=api_key)
                relative = source.relative_to(data_dir)
                output = destination / relative.with_name(relative.name.removesuffix(SUFFIX))
                changed += upsert_jsonl(output, rows, key_fields=keys, replace=True)
                # Forecasts contain licensed data; keep exports private to the local user.
                if output.exists():
                    output.chmod(0o600)
            except Exception as exc:
                failures.append(f"{source.name}: {exc}")
    return changed, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    args = parser.parse_args()
    key = os.getenv("VISUALCROSSING_API_KEY")
    if not key:
        parser.error("Set VISUALCROSSING_API_KEY to the key used to encrypt the archives")
    changed, failures = export_forecasts(args.data_dir, key)
    print(f"Private forecast rows exported: {changed} (under {args.data_dir / 'private/visual_crossing'})")
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
