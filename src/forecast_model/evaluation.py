"""Simple baselines and the error table every method is scored with."""
from __future__ import annotations

import numpy as np
import pandas as pd

from forecast_model.data import CITY, LABELS, SOURCES


def climatology(history: pd.DataFrame, target: str) -> pd.Series:
    return history.groupby(["station_id", "week"])[target].mean()


def baselines(frame: pd.DataFrame, metric: str, clim: pd.Series) -> dict[str, pd.Series]:
    out = {LABELS[s]: frame[f"{s}_{metric}_f"] for s in SOURCES}
    out["Average of 6 sources"] = frame["ens_mean"]
    out["Average, bias-corrected (30d)"] = frame[[f"{s}_debiased" for s in SOURCES]].mean(axis=1)
    out["Persistence (yesterday)"] = frame["persistence"]
    keys = pd.MultiIndex.from_frame(frame[["station_id", "week"]])
    out["Climatology"] = pd.Series(clim.reindex(keys).to_numpy(), frame.index)
    return out


def score(methods: dict[str, pd.Series], actual: pd.Series, stations: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for name, pred in methods.items():
        valid = pred.notna()
        err = pred[valid] - actual[valid]
        # Observations are whole degrees; round every method so integer forecasts (MOS)
        # and continuous ones (ML) are scored the same way on exact/within-1 hits.
        rounded = np.round(pred[valid]) - actual[valid]
        rows.append({"method": name, "mae": float(np.mean(np.abs(err))),
                     "rmse": float(np.sqrt(np.mean(err ** 2))), "bias": float(err.mean()),
                     "exact": float(np.mean(rounded == 0)), "within_1f": float(np.mean(np.abs(rounded) <= 1)),
                     "n": int(valid.sum())})
    table = pd.DataFrame(rows).sort_values("mae").reset_index(drop=True)
    per_city = pd.DataFrame({name: (pred - actual).abs().groupby(stations).mean()
                             for name, pred in methods.items()}).rename(index=CITY)
    return table, per_city
