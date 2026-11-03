"""Engineered features. Every feature uses only information from before the target day."""
from __future__ import annotations

import numpy as np
import pandas as pd

from forecast_model.data import SOURCES


def target_column(metric: str) -> str:
    return f"obs_{metric}_f"


def build_features(df: pd.DataFrame, metric: str) -> tuple[pd.DataFrame, list[str]]:
    df = df.sort_values(["station_id", "date"]).copy()
    target = target_column(metric)
    cols = [f"{s}_{metric}_f" for s in SOURCES]
    features = list(cols)

    forecasts = df[cols]
    df["ens_mean"] = forecasts.mean(axis=1)
    df["ens_median"] = forecasts.median(axis=1)
    df["ens_spread"] = forecasts.std(axis=1)
    df["ens_range"] = forecasts.max(axis=1) - forecasts.min(axis=1)
    features += ["ens_mean", "ens_median", "ens_spread", "ens_range"]

    for source, col in zip(SOURCES, cols):
        # Only errors from days that have already ended (shift 1) can be used.
        error = (df[col] - df[target]).groupby(df["station_id"]).shift(1)
        for window in (7, 30):
            name = f"{source}_bias_{window}d"
            df[name] = error.groupby(df["station_id"]).transform(
                lambda s, w=window: s.rolling(w, min_periods=max(3, w // 3)).mean())
            features.append(name)
        df[f"{source}_debiased"] = df[col] - df[f"{source}_bias_30d"]
        features.append(f"{source}_debiased")

    df["persistence"] = df.groupby("station_id")[target].shift(1)
    day = pd.to_datetime(df["date"]).dt.day_of_year
    df["doy_sin"], df["doy_cos"] = np.sin(2 * np.pi * day / 365.25), np.cos(2 * np.pi * day / 365.25)
    df["week"] = pd.to_datetime(df["date"]).dt.isocalendar().week.astype(int)
    features += ["persistence", "doy_sin", "doy_cos"]
    return df, features
