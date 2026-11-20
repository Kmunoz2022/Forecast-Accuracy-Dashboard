"""Train the forecast models and evaluate them on unseen dates and against Kalshi.

Runs once per prediction cutoff (see forecast_model/data.py):
  open      11 AM ET the day before, one hour after Kalshi opens (strict, main result)
  midnight  midnight before the measured day (fresher forecasts, for comparison)

For each cutoff:
1. Builds features from sources published by the cutoff; observations lag two days.
2. Tunes Ridge regression and gradient boosting on the validation period, then
   refits each on train + validation and predicts the test period once.
3. Scores every method on the same station-days against single sources and baselines.
4. Model 3: learns a per-day uncertainty around each model's prediction and scores it
   against the fixed per-city uncertainty, on test days and on every settled Kalshi
   bracket (no prices needed).
5. Turns daily-high predictions into bracket probabilities and compares them with
   Kalshi prices snapshotted at the same cutoff (src/backfill_prices.py --cutoff).

Writes outputs/<cutoff>/metrics.json, kalshi_test_markets.csv and figures/.
Needs the analysis packages: pip install pandas numpy scikit-learn matplotlib
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from forecast_model import markets, plots
from forecast_model.data import CITY, CUTOFFS, TEST_START, VAL_START, Cutoff, load_dataset, split
from forecast_model.evaluation import baselines, climatology, score
from forecast_model.features import build_features, target_column, uncertainty_features
from forecast_model.models import FittedFamily, candidates, fit_family
from forecast_model.uncertainty import FittedSigma, fit_sigma, prob_scores


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAMILY_LABELS = {"ridge": "ML: Ridge regression", "boost": "ML: Gradient boosting"}


def evaluate_metric(raw: pd.DataFrame, metric: str, cutoff: Cutoff, history_dir: Path,
                    figures: Path) -> tuple[dict, pd.DataFrame]:
    df, features = build_features(raw, metric, cutoff)
    df, sigma_features = uncertainty_features(df, metric, cutoff)
    target = target_column(metric)
    train, val, test = split(df.dropna(subset=[target]))
    columns = features + ["station_id"]
    families = candidates(features)
    fitted = {family: fit_family(models, columns, target, train, val, test)
              for family, models in families.items()}
    # Model 3: a per-day uncertainty around each family's chosen model.
    sigma3 = {family: fit_sigma(families[family][fit.name], columns, target, sigma_features, train, val, test)
              for family, fit in fitted.items()}

    methods = {FAMILY_LABELS[f]: fit.test_pred for f, fit in fitted.items()}
    methods.update(baselines(test, metric, cutoff, climatology(pd.concat([train, val]), target)))
    table, per_city = score(methods, test[target], test["station_id"])

    if metric == "high":
        history = pd.concat([train, val])
        plots.bias_by_city(history, metric, cutoff.sources, figures / "eda_bias_by_city.png")
        plots.error_by_month(history, metric, cutoff.sources, figures / "eda_error_by_month.png")
        plots.spread_vs_error(history, metric, figures / "eda_spread_vs_error.png")
    plots.method_errors(table, f"Test period, {cutoff.name} cutoff: daily {metric}, all 8 cities",
                        figures / f"test_mae_{metric}.png")

    predictions = test[["station_id", "date"]].assign(
        **{f"pred_{f}": fit.test_pred for f, fit in fitted.items()},
        **{f"sigma_{f}": s.test_sigma for f, s in sigma3.items()})
    result = {"table": table.to_dict("records"), "per_city": per_city.round(2).to_dict(),
              "chosen": {f: fit.name for f, fit in fitted.items()},
              "val_mae": {f: fit.val_mae for f, fit in fitted.items()},
              "sigma": {f: fit.sigma for f, fit in fitted.items()},
              "uncertainty": evaluate_uncertainty(test, target, metric, cutoff, fitted, sigma3,
                                                  history_dir, figures),
              "sizes": {"train": len(train), "val": len(val), "test": len(test)}}
    return result, predictions


def evaluate_uncertainty(test: pd.DataFrame, target: str, metric: str, cutoff: Cutoff,
                         fitted: dict[str, FittedFamily], sigma3: dict[str, FittedSigma],
                         history_dir: Path, figures: Path) -> dict:
    """Model 3 against the fixed per-city sigma, on test days and on settled Kalshi brackets."""
    obs, stations = test[target], test["station_id"]
    sigmas = {f: {"fixed": stations.map(fit.sigma).clip(lower=markets.MIN_SIGMA), "model3": sigma3[f].test_sigma}
              for f, fit in fitted.items()}
    nus = {f: {"fixed": None, "model3": sigma3[f].nu} for f in fitted}
    out = {"families": {f: {"chosen": sigma3[f].label, "nu": sigma3[f].nu,
                            "val_log_score": sigma3[f].val_log_score,
                            "test": {kind: prob_scores(obs, fit.test_pred, sigmas[f][kind], nus[f][kind])
                                     for kind in ("fixed", "model3")}}
                        for f, fit in fitted.items()}}
    plots.sigma_reliability(obs - fitted["ridge"].test_pred, stations, sigmas["ridge"]["fixed"],
                            sigmas["ridge"]["model3"], f"Is the uncertainty right? Ridge, daily {metric}, "
                            f"{cutoff.name} cutoff", figures / f"sigma_reliability_{metric}.png")

    m = markets.load_settled_markets(history_dir, metric)
    if not m.empty:
        m = m.merge(test[["station_id", "date"]].assign(
            **{f"mu_{f}": fit.test_pred for f, fit in fitted.items()},
            **{f"sigma_{kind}_{f}": s for f in fitted for kind, s in sigmas[f].items()}),
            on=["station_id", "date"])
    if m.empty:
        out["settled_brackets"] = None
        return out
    for f in fitted:
        for kind in ("fixed", "model3"):
            m = markets.add_model_probs(m, f"p_{kind}_{f}", m[f"mu_{f}"], m[f"sigma_{kind}_{f}"], nus[f][kind])
    out["settled_brackets"] = {
        "events": int(m.event_ticker.nunique()), "brackets": int(len(m)),
        "cities": sorted(CITY[s] for s in m.station_id.unique()),
        **{f"{kind}_{f}": markets.scores(m, f"p_{kind}_{f}") for f in fitted for kind in ("fixed", "model3")}}
    plots.calibration(m, [("p_fixed_ridge", "Ridge, fixed σ per city", plots.BASE),
                          ("p_model3_ridge", "Ridge + Model 3", plots.ACCENT)],
                      f"Settled Kalshi brackets, daily {metric}, {cutoff.name} cutoff",
                      figures / f"bracket_calibration_{metric}.png")
    return out


def evaluate_kalshi(high: dict, predictions: pd.DataFrame, cutoff: Cutoff, history: Path,
                    out_dir: Path, edge: float) -> dict | None:
    m = markets.load_priced_markets(history, cutoff.prices_dir)
    if m.empty:
        return None
    m = m.merge(predictions, on=["station_id", "date"])
    model3 = high["uncertainty"]["families"]
    for family in ("ridge", "boost"):
        m = markets.add_model_probs(m, f"model_p_{family}", m[f"pred_{family}"],
                                    m.station_id.map(high["sigma"][family]))
        m = markets.add_model_probs(m, f"model_p_{family}_m3", m[f"pred_{family}"], m[f"sigma_{family}"],
                                    model3[family]["nu"])
    m = markets.complete_events(m)

    results = {"events": int(m.event_ticker.nunique()), "markets": int(len(m)),
               "cities": sorted(CITY[s] for s in m.station_id.unique()),
               "market": markets.scores(m, "market_p")}
    for name in ("ridge", "boost", "ridge_m3", "boost_m3"):
        trading, trades = markets.paper_trade(m, f"model_p_{name}", edge)
        trading["by_city"] = (trades.groupby("station_id").pnl.agg(["count", "sum"])
                              .rename(index=CITY).round(2).to_dict("index"))
        results[name] = {**markets.scores(m, f"model_p_{name}"), "paper_trading": trading}
    results["blend"] = markets.blend_test(m, "model_p_ridge", holdout_start="2026-06-01")
    results["blend_m3"] = markets.blend_test(m, "model_p_ridge_m3", holdout_start="2026-06-01")

    plots.calibration(m, [("market_p", "Kalshi market", plots.MARKET),
                          ("model_p_ridge", "Ridge, fixed σ per city", plots.BASE),
                          ("model_p_ridge_m3", "Ridge + Model 3", plots.ACCENT)],
                      f"Against Kalshi prices, {cutoff.name} cutoff",
                      out_dir / "figures" / "kalshi_calibration.png")
    m.to_csv(out_dir / "kalshi_test_markets.csv", index=False)
    return results


def evaluate_cutoff(raw: pd.DataFrame, cutoff: Cutoff, history: Path, out_dir: Path, edge: float) -> dict:
    figures = out_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    high, high_predictions = evaluate_metric(raw, "high", cutoff, history, figures)
    low, _ = evaluate_metric(raw, "low", cutoff, history, figures)
    kalshi = evaluate_kalshi(high, high_predictions, cutoff, history, out_dir, edge)
    report = {"cutoff": cutoff.name, "description": cutoff.description, "sources": list(cutoff.sources),
              "high": high, "low": low, "kalshi_high": kalshi,
              "split": {"validation_start": VAL_START, "test_start": TEST_START}}
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2, default=float))
    return report


def print_report(report: dict) -> None:
    print(f"\n######## Cutoff: {report['description']} ########")
    for name in ("high", "low"):
        rows = report[name]["table"]
        print(f"\n== {name.upper()}: test-period error, °F (n = {rows[0]['n']} station-days each) ==")
        for row in rows:
            print(f"{row['method']:32} MAE {row['mae']:.2f}  bias {row['bias']:+.2f}  "
                  f"exact {row['exact']:.0%}  within 1°F {row['within_1f']:.0%}")
        print_uncertainty(report[name]["uncertainty"])
    kalshi = report["kalshi_high"]
    if kalshi:
        print(f"\n== Kalshi daily highs: {kalshi['events']} days, {len(kalshi['cities'])} cities ==")
        for side in ("market", "ridge", "boost", "ridge_m3", "boost_m3"):
            s = kalshi[side]
            print(f"{side:8} picked winner {s['top_bracket_hit_rate']:.1%}  brier {s['brier']:.4f}  "
                  f"log loss {s['log_loss']:.3f}")
        for blend in ("blend", "blend_m3"):
            print(f"{blend}: weight on market (chosen before {kalshi[blend]['holdout_start']}): "
                  f"{kalshi[blend]['market_weight']}")
    else:
        print(f"\n(no Kalshi prices for this cutoff yet; run src/backfill_prices.py --cutoff {report['cutoff']})")


def print_uncertainty(u: dict) -> None:
    print("-- Model 3, per-day uncertainty vs fixed per city (test period; lower log score is better) --")
    for family, r in u["families"].items():
        fixed, m3 = r["test"]["fixed"], r["test"]["model3"]
        print(f"{family:5} [{r['chosen']}]  log score {fixed['log_score']:.3f} -> {m3['log_score']:.3f}  "
              f"80% interval hit {fixed['coverage_80']:.0%} -> {m3['coverage_80']:.0%}")
    s = u["settled_brackets"]
    if s:
        print(f"Settled Kalshi brackets, {s['events']} days in {len(s['cities'])} cities (no prices needed):")
        for family in u["families"]:
            fixed, m3 = s[f"fixed_{family}"], s[f"model3_{family}"]
            print(f"{family:5} log loss {fixed['log_loss']:.3f} -> {m3['log_loss']:.3f}  "
                  f"brier {fixed['brier']:.4f} -> {m3['brier']:.4f}  "
                  f"picked winner {fixed['top_bracket_hit_rate']:.1%} -> {m3['top_bracket_hit_rate']:.1%}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "data/history/dataset.csv")
    parser.add_argument("--history-dir", type=Path, default=PROJECT_ROOT / "data/history")
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "outputs")
    parser.add_argument("--cutoffs", nargs="+", choices=CUTOFFS, default=list(CUTOFFS))
    parser.add_argument("--edge", type=float, default=0.10, help="Paper-trading probability edge")
    args = parser.parse_args()

    raw = load_dataset(args.dataset)
    for name in args.cutoffs:
        report = evaluate_cutoff(raw, CUTOFFS[name], args.history_dir, args.out_dir / name, args.edge)
        print_report(report)
    print(f"\nWrote {args.out_dir}/<cutoff>/metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
