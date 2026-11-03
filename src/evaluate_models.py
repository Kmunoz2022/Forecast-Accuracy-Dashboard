"""Train the forecast models and evaluate them on unseen dates and against Kalshi.

1. Builds features and splits by time (see forecast_model/data.py).
2. Tunes Ridge regression and gradient boosting on the validation period, then
   refits each on train + validation and predicts the test period once.
3. Scores both against every single source and simple baselines.
4. Turns daily-high predictions into bracket probabilities and compares them with
   Kalshi prices taken at the start of each measured day (src/backfill_prices.py).

Writes outputs/metrics.json, outputs/kalshi_test_markets.csv and outputs/figures/.
Needs the analysis packages: pip install pandas numpy scikit-learn matplotlib
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from forecast_model import markets, plots
from forecast_model.data import CITY, TEST_START, VAL_START, load_dataset, split
from forecast_model.evaluation import baselines, climatology, score
from forecast_model.features import build_features, target_column
from forecast_model.models import candidates, fit_family


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAMILY_LABELS = {"ridge": "ML: Ridge regression", "boost": "ML: Gradient boosting"}


def evaluate_metric(raw: pd.DataFrame, metric: str, figures: Path) -> tuple[dict, pd.DataFrame]:
    df, features = build_features(raw, metric)
    target = target_column(metric)
    train, val, test = split(df.dropna(subset=[target]))
    columns = features + ["station_id"]
    fitted = {family: fit_family(models, columns, target, train, val, test)
              for family, models in candidates(features).items()}

    methods = {FAMILY_LABELS[f]: fit.test_pred for f, fit in fitted.items()}
    methods.update(baselines(test, metric, climatology(pd.concat([train, val]), target)))
    table, per_city = score(methods, test[target], test["station_id"])

    if metric == "high":
        history = pd.concat([train, val])
        plots.bias_by_city(history, metric, figures / "eda_bias_by_city.png")
        plots.error_by_month(history, metric, figures / "eda_error_by_month.png")
        plots.spread_vs_error(history, metric, figures / "eda_spread_vs_error.png")
    plots.method_errors(table, f"Test period: daily {metric} temperature, all 8 cities",
                        figures / f"test_mae_{metric}.png")

    predictions = test[["station_id", "date"]].assign(
        **{f"pred_{f}": fit.test_pred for f, fit in fitted.items()})
    result = {"table": table.to_dict("records"), "per_city": per_city.round(2).to_dict(),
              "chosen": {f: fit.name for f, fit in fitted.items()},
              "val_mae": {f: fit.val_mae for f, fit in fitted.items()},
              "sigma": {f: fit.sigma for f, fit in fitted.items()},
              "sizes": {"train": len(train), "val": len(val), "test": len(test)}}
    return result, predictions


def evaluate_kalshi(high: dict, predictions: pd.DataFrame, history: Path, out_dir: Path, edge: float) -> dict | None:
    m = markets.load_priced_markets(history)
    if m.empty:
        return None
    m = m.merge(predictions, on=["station_id", "date"])
    for family in ("ridge", "boost"):
        m = markets.add_model_probs(m, f"model_p_{family}", m[f"pred_{family}"], high["sigma"][family])
    m = markets.complete_events(m)

    results = {"events": int(m.event_ticker.nunique()), "markets": int(len(m)),
               "cities": sorted(CITY[s] for s in m.station_id.unique()),
               "market": markets.scores(m, "market_p")}
    for family in ("ridge", "boost"):
        trading, trades = markets.paper_trade(m, f"model_p_{family}", edge)
        trading["by_city"] = (trades.groupby("station_id").pnl.agg(["count", "sum"])
                              .rename(index=CITY).round(2).to_dict("index"))
        results[family] = {**markets.scores(m, f"model_p_{family}"), "paper_trading": trading}
    results["blend"] = markets.blend_test(m, "model_p_ridge", holdout_start="2026-06-01")

    plots.calibration(m, "model_p_boost", out_dir / "figures" / "kalshi_calibration.png")
    m.to_csv(out_dir / "kalshi_test_markets.csv", index=False)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "data/history/dataset.csv")
    parser.add_argument("--history-dir", type=Path, default=PROJECT_ROOT / "data/history")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "outputs")
    parser.add_argument("--edge", type=float, default=0.10, help="Paper-trading probability edge")
    args = parser.parse_args()
    figures = args.out_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    raw = load_dataset(args.dataset)
    high, high_predictions = evaluate_metric(raw, "high", figures)
    low, _ = evaluate_metric(raw, "low", figures)
    kalshi = evaluate_kalshi(high, high_predictions, args.history_dir, args.out_dir, args.edge)

    report = {"high": high, "low": low, "kalshi_high": kalshi,
              "split": {"validation_start": VAL_START, "test_start": TEST_START}}
    (args.out_dir / "metrics.json").write_text(json.dumps(report, indent=2, default=float))

    for name, result in (("HIGH", high), ("LOW", low)):
        print(f"\n== {name}: test-period error, °F ==")
        for row in result["table"]:
            print(f"{row['method']:32} MAE {row['mae']:.2f}  bias {row['bias']:+.2f}  "
                  f"exact {row['exact']:.0%}  within 1°F {row['within_1f']:.0%}")
    if kalshi:
        print(f"\n== Kalshi daily highs: {kalshi['events']} days, {', '.join(kalshi['cities'])} ==")
        for side in ("market", "ridge", "boost"):
            s = kalshi[side]
            print(f"{side:7} picked winner {s['top_bracket_hit_rate']:.1%}  brier {s['brier']:.4f}  "
                  f"log loss {s['log_loss']:.3f}")
        print(f"Blend weight on market (chosen before {kalshi['blend']['holdout_start']}): "
              f"{kalshi['blend']['market_weight']}")
    print(f"\nWrote {args.out_dir / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
