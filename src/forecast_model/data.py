"""Dataset loading, source names, and the fixed time split."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


SOURCES = ["mos", "gfs_day1", "hrrr_day1", "icon_day1", "gem_day1", "ecmwf_day1"]
LABELS = {"mos": "NWS MOS", "gfs_day1": "GFS", "hrrr_day1": "HRRR", "icon_day1": "ICON",
          "gem_day1": "GEM", "ecmwf_day1": "ECMWF"}
CITY = {"nyc_central_park": "New York", "los_angeles_lax": "Los Angeles", "chicago_midway": "Chicago",
        "miami_international": "Miami", "dallas_fort_worth": "Dallas", "seattle_tacoma": "Seattle",
        "boston_logan": "Boston", "san_francisco_sfo": "San Francisco"}

# Split by time, never randomly. START is the first date with all six sources.
# The test period is scored once, after every choice is made on validation.
START, VAL_START, TEST_START = "2024-02-05", "2025-07-01", "2026-01-01"


def load_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "hrrr_day1_high_f" not in df:
        raise ValueError(f"{path} predates the single-model fix; run src/build_dataset.py")
    return df


def split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = df[df["date"] >= START]
    return (df[df["date"] < VAL_START],
            df[(df["date"] >= VAL_START) & (df["date"] < TEST_START)],
            df[df["date"] >= TEST_START])
