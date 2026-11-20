"""Charts for the report. Each function writes one PNG."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from forecast_model.data import CITY, label

ACCENT, BASE, MARKET = "#2a78d6", "#9aa5b1", "#eb6834"  # our model, baseline, Kalshi market


def bias_by_city(df: pd.DataFrame, metric: str, sources, path: Path) -> None:
    target = f"obs_{metric}_f"
    bias = pd.DataFrame({label(s): (df[f"{s}_{metric}_f"] - df[target]).groupby(df["station_id"]).mean()
                         for s in sources}).rename(index=CITY)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    im = ax.imshow(bias.values, cmap="RdBu_r", vmin=-5, vmax=5)
    ax.set_xticks(range(len(bias.columns)), [c.replace(" ", "\n") for c in bias.columns])
    ax.set_yticks(range(len(bias)), bias.index)
    for i in range(bias.shape[0]):
        for j in range(bias.shape[1]):
            ax.text(j, i, f"{bias.values[i, j]:+.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, label="Average error, °F (red = too warm)")
    ax.set_title("Each source has consistent, city-specific bias")
    _save(fig, path)


def error_by_month(df: pd.DataFrame, metric: str, sources, path: Path) -> None:
    target = f"obs_{metric}_f"
    month = pd.to_datetime(df["date"]).dt.month
    fig, ax = plt.subplots(figsize=(8, 4))
    for s in sources:
        ax.plot(range(1, 13), (df[f"{s}_{metric}_f"] - df[target]).abs().groupby(month).mean(), label=label(s))
    ax.set_xticks(range(1, 13))
    ax.set_xlabel("Month")
    ax.set_ylabel("Mean absolute error, °F")
    ax.set_title("Forecast error by month")
    ax.legend(ncol=3, fontsize=8)
    _save(fig, path)


def spread_vs_error(df: pd.DataFrame, metric: str, path: Path) -> None:
    err = (df["ens_mean"] - df[f"obs_{metric}_f"]).abs()
    grouped = err.groupby(pd.qcut(df["ens_spread"], 5), observed=True).mean()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar([f"{b.left:.1f}-{b.right:.1f}" for b in grouped.index], grouped.values, color=ACCENT)
    ax.set_xlabel("Disagreement between sources (std dev, °F)")
    ax.set_ylabel("Error of the average, °F")
    ax.set_title("When sources disagree, forecasts are less accurate")
    _save(fig, path)


def method_errors(table: pd.DataFrame, title: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    rows = table[::-1]
    ax.barh(rows["method"], rows["mae"], color=[ACCENT if m.startswith("ML") else BASE for m in rows["method"]])
    ax.set_xlabel("Mean absolute error, °F (lower is better)")
    ax.set_title(title)
    for y, v in enumerate(rows["mae"]):
        ax.text(v + 0.03, y, f"{v:.2f}", va="center", fontsize=9)
    _save(fig, path)


def calibration(m: pd.DataFrame, series: list[tuple[str, str, str]], title: str, path: Path) -> None:
    """Reliability diagram for bracket probabilities; `series` holds (column, label, color)."""
    fig, ax = plt.subplots(figsize=(5.5, 5))
    bins = np.linspace(0, 1, 11)
    ax.plot([0, 1], [0, 1], "--", color="#555", lw=1, label="Perfectly calibrated")
    for col, label, color in series:
        groups = m.groupby(pd.cut(m[col], bins), observed=True)
        ax.plot(groups[col].mean(), groups.won.mean(), "o-", label=label, color=color, lw=2, ms=6)
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Actual win rate")
    ax.set_title(title)
    ax.grid(color="#e6e6e6", lw=0.8)
    ax.set_axisbelow(True)
    ax.legend()
    _save(fig, path)


def sigma_reliability(error: pd.Series, stations: pd.Series, fixed: pd.Series, per_day: pd.Series,
                      title: str, path: Path) -> None:
    """Predicted sigma against the error that followed. On the diagonal, sigma is right."""
    fig, ax = plt.subplots(figsize=(6, 5))
    rmse = lambda e: float(np.sqrt(np.mean(e ** 2)))  # noqa: E731
    by_city = pd.DataFrame({"sigma": fixed, "error": error}).groupby(stations)
    deciles = pd.DataFrame({"sigma": per_day, "error": error}).groupby(pd.qcut(per_day, 10), observed=True)
    top = 0.0
    for groups, label, color, style in ((by_city, "Fixed σ per city (one point per city)", BASE, "o"),
                                        (deciles, "Model 3: σ per day (tenths of days)", ACCENT, "o-")):
        x, y = groups.sigma.mean(), groups.error.agg(rmse)
        order = np.argsort(x.to_numpy())
        ax.plot(x.to_numpy()[order], y.to_numpy()[order], style, label=label, color=color, lw=2, ms=6)
        top = max(top, x.max(), y.max())
    top = float(np.ceil(top + 0.5))
    ax.plot([0, top], [0, top], "--", color="#555", lw=1, label="Predicted = actual")
    ax.set_xlim(0, top)
    ax.set_ylim(0, None)
    ax.set_xlabel("Predicted uncertainty σ, °F")
    ax.set_ylabel("Actual error (RMS), °F")
    ax.set_title(title)
    ax.grid(color="#e6e6e6", lw=0.8)
    ax.set_axisbelow(True)
    ax.legend(fontsize=8, loc="upper left")
    _save(fig, path)


def _save(fig, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
