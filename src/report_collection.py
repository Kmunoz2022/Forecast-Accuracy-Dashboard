"""Build collection health and aggregate accuracy reports without network requests."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from collect_forecasts import parse_as_of
from weather_pipeline.config import load_stations
from weather_pipeline.live_report import build_reports, markdown_summary
from weather_pipeline.storage import write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=Path, default=ROOT / "config/stations.csv")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "reports/live")
    parser.add_argument("--as-of")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--github-summary", action="store_true")
    args = parser.parse_args()
    if args.lookback_days < 1:
        parser.error("--lookback-days must be positive")
    health, accuracy = build_reports(args.data_dir, load_stations(args.stations), parse_as_of(args.as_of),
                                     lookback_days=args.lookback_days)
    write_json(args.out_dir / "health.json", health)
    write_json(args.out_dir / "accuracy.json", accuracy)
    summary = markdown_summary(health, accuracy)
    (args.out_dir / "summary.md").write_text(summary, encoding="utf-8")
    if args.github_summary and os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary)
    return int(any(p["reason"] != "decryption_key_unavailable" for p in health["archive_problems"]))


if __name__ == "__main__":
    raise SystemExit(main())
