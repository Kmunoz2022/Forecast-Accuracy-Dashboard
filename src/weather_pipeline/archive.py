"""Read live collection archives, keeping restricted forecasts in memory."""
from __future__ import annotations

import os
from pathlib import Path

from weather_pipeline.storage import read_jsonl


def load_category(root: Path, category: str, *, key: str | None = None) -> tuple[list[dict], list[dict]]:
    rows, problems = [], []
    key = key or os.getenv("VISUALCROSSING_API_KEY")
    for path in sorted((root / category).rglob("*.jsonl*")):
        if path.name.endswith(".tmp") or path.suffix not in {".jsonl", ".enc"}:
            continue
        encrypted = path.name.endswith(".visual_crossing.enc")
        if path.suffix == ".enc" and not encrypted:
            problems.append({"category": category, "file": path.name, "reason": "unknown_archive_format"})
            continue
        if encrypted and not key:
            problems.append({"category": category, "file": path.name, "provider": "visual_crossing",
                             "reason": "decryption_key_unavailable"})
            continue
        try:
            records = read_jsonl(path, encryption_key=key if encrypted else None)
            if not all(isinstance(row, dict) for row in records):
                raise ValueError("Archive rows must be objects")
            rows.extend(records)
        except (ValueError, OSError, ImportError):
            # Never echo decrypted data, keys, or parse-error contents into public reports.
            problems.append({"category": category, "file": path.name,
                             "provider": "visual_crossing" if encrypted else "unknown",
                             "reason": "archive_unreadable"})
    return rows, problems


def forecast_targets(root: Path) -> set[tuple[str, str]]:
    """Only station-dates with a saved forecast, including encrypted-only successes."""
    targets = set()
    for category in ("forecasts", "market_forecasts"):
        rows, _ = load_category(root, category)
        targets.update((r["station_id"], r["target_date_local"]) for r in rows)
    for category in ("daily_collection", "market_collection"):
        rows, _ = load_category(root, category)
        targets.update((r["station_id"], r["target_date_local"]) for r in rows
                       if r.get("status") in {"saved", "existing", "near_open", "late"})
    return targets
