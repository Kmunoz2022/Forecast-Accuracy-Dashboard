from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import numpy as np
    import pandas as pd
    import sklearn  # noqa: F401
except ImportError:  # The collection workflows install only tzdata.
    pd = None

if pd is not None:
    from scipy import stats
    from sklearn.base import BaseEstimator

    from forecast_model.data import CUTOFFS, START, TEST_START, VAL_START, split
    from forecast_model.evaluation import score
    from forecast_model.features import build_features, uncertainty_features
    from forecast_model.uncertainty import (EMBARGO_DAYS, integer_prob, out_of_fold, predict_sigma,
                                            prob_scores, sigma_candidates)

    CUTOFF = CUTOFFS["midnight"]
    from forecast_model.markets import bracket_prob, complete_events, normalized_blend, std_cdf


def synthetic(days: int = 60) -> "pd.DataFrame":
    dates = pd.date_range("2024-01-01", periods=days).strftime("%Y-%m-%d")
    rng = np.random.default_rng(0)
    rows = []
    for station in ("nyc_central_park", "miami_international"):
        obs = 60 + rng.normal(0, 5, days)
        row = {"station_id": station, "date": dates, "obs_high_f": obs.round()}
        for i, source in enumerate(CUTOFF.sources):
            row[f"{source}_high_f"] = obs + i - 2 + rng.normal(0, 1, days)
        for source, earlier in CUTOFF.revisions:
            row[f"{earlier}_high_f"] = row[f"{source}_high_f"] + rng.normal(0, 1, days)
        rows.append(pd.DataFrame(row))
    return pd.concat(rows, ignore_index=True)


def all_features(df: "pd.DataFrame") -> tuple["pd.DataFrame", list[str]]:
    df, features = build_features(df, "high", CUTOFF)
    df, sigma_features = uncertainty_features(df, "high", CUTOFF)
    return df, features + sigma_features


@unittest.skipIf(pd is None, "analysis packages not installed")
class FeatureTests(unittest.TestCase):
    def test_features_never_use_unpublished_observations(self):
        # Predicting Feb 15: neither Feb 15 nor Feb 14 is officially reported yet.
        for changed_day in ("2024-02-15", "2024-02-14"):
            with self.subTest(changed_day=changed_day):
                df = synthetic()
                changed = df.copy()
                day = (changed.station_id == "nyc_central_park") & (changed.date == changed_day)
                changed.loc[day, "obs_high_f"] += 100
                before, features = all_features(df)
                after, _ = all_features(changed)
                row = (before.station_id == "nyc_central_park") & (before.date == "2024-02-15")
                pd.testing.assert_series_equal(before.loc[row, features].iloc[0],
                                               after.loc[row, features].iloc[0])

    def test_trailing_bias_matches_hand_calculation(self):
        df, _ = build_features(synthetic(), "high", CUTOFF)
        nyc = df[df.station_id == "nyc_central_park"].reset_index(drop=True)
        errors = nyc["mos_high_f"] - nyc["obs_high_f"]
        # Day 20 may use errors from days 12-18: the last 7 days published two days earlier.
        self.assertAlmostEqual(nyc.loc[20, "mos_bias_7d"], errors[12:19].mean())

    def test_bias_does_not_leak_between_cities(self):
        df, _ = all_features(synthetic())
        first_miami = df[df.station_id == "miami_international"].iloc[0]
        self.assertTrue(np.isnan(first_miami["mos_bias_7d"]))
        self.assertTrue(np.isnan(first_miami["persistence"]))
        self.assertTrue(np.isnan(first_miami["recent_abs_err_7d"]))

    def test_revisions_compare_earlier_runs_published_by_the_cutoff(self):
        def lead(source):  # days ahead; MOS 00Z is the earlier run of the same day as 12Z
            return {"mos": 1, "mos00": 1.5}.get(source) or int(source.rsplit("day", 1)[1])

        for cutoff in CUTOFFS.values():
            for source, earlier in cutoff.revisions:
                self.assertIn(source, cutoff.sources)
                self.assertEqual(source.split("_")[0], earlier.split("_")[0].replace("mos00", "mos"))
                self.assertGreater(lead(earlier), lead(source))
        # One hour after Kalshi opens, no day-1 run or 12Z MOS exists yet.
        used = {s for pair in CUTOFFS["open"].revisions for s in pair} | set(CUTOFFS["open"].sources)
        self.assertFalse({s for s in used if s == "mos" or s.endswith("_day1")})

    def test_revision_is_mean_absolute_change_between_runs(self):
        df, _ = all_features(synthetic())
        row = df.iloc[10]
        change = [row[f"{s}_high_f"] - row[f"{e}_high_f"] for s, e in CUTOFF.revisions]
        self.assertAlmostEqual(row["log_revision"], np.log1p(np.mean(np.abs(change))))
        self.assertAlmostEqual(row["log_consensus_shift"], np.log1p(abs(np.mean(change))))


@unittest.skipIf(pd is None, "analysis packages not installed")
class ScoringTests(unittest.TestCase):
    def test_all_methods_are_scored_on_the_same_days(self):
        actual = pd.Series([70.0, 71, 72, 73])
        methods = {"complete": pd.Series([70.0, 70, 70, 70]),
                   "gappy": pd.Series([70.0, None, 72, 73])}
        table, _ = score(methods, actual, pd.Series(["nyc_central_park"] * 4))
        self.assertEqual(set(table.n), {3})  # the day "gappy" missed is dropped for both
        self.assertAlmostEqual(table.set_index("method").loc["complete", "mae"], (0 + 2 + 3) / 3)


@unittest.skipIf(pd is None, "analysis packages not installed")
class SplitTests(unittest.TestCase):
    def test_periods_are_ordered_and_disjoint(self):
        dates = pd.date_range("2023-06-01", "2026-10-28").strftime("%Y-%m-%d")
        train, val, test = split(pd.DataFrame({"date": dates}))
        self.assertEqual(train.date.min(), START)
        self.assertLess(train.date.max(), val.date.min())
        self.assertLess(val.date.max(), test.date.min())
        self.assertEqual((val.date.min(), test.date.min()), (VAL_START, TEST_START))
        self.assertEqual(len(train) + len(val) + len(test), (dates >= START).sum())


@unittest.skipIf(pd is None, "analysis packages not installed")
class MarketTests(unittest.TestCase):
    # A typical Kalshi day: two tails and four 2°F brackets covering every integer.
    BRACKETS = [("less", None, 70), ("between", 70, 71), ("between", 72, 73), ("between", 74, 75),
                ("between", 76, 77), ("greater", 77, None)]

    def test_bracket_probabilities_sum_to_one(self):
        for mu, sigma in ((73.4, 1.7), (65.0, 2.0), (80.2, 0.8)):
            for nu in (None, 5, 12):
                total = sum(bracket_prob(kind, floor, cap, mu, sigma, nu) for kind, floor, cap in self.BRACKETS)
                self.assertAlmostEqual(total, 1.0, places=9)

    def test_student_t_has_unit_variance_and_heavier_tails(self):
        # sigma keeps meaning the standard deviation when the tail shape changes.
        unit_t = stats.t(df=5, scale=np.sqrt(3 / 5))
        self.assertAlmostEqual(unit_t.var(), 1.0)
        for x in (-3.0, -1.0, 0.4, 2.5):
            self.assertAlmostEqual(float(std_cdf(x, 5)), unit_t.cdf(x), places=9)
        self.assertGreater(float(std_cdf(-3.0, 5)), float(std_cdf(-3.0)))

    def test_bracket_covers_whole_degrees_it_settles_on(self):
        # 72-73 settles yes on 72 or 73, i.e. a true value in [71.5, 73.5).
        p = bracket_prob("between", 72, 73, 72.5, 1.0)
        self.assertAlmostEqual(p, 0.6826894921, places=6)
        self.assertGreater(bracket_prob("greater", 77, None, 80, 1.0), 0.99)
        self.assertLess(bracket_prob("less", None, 70, 80, 1.0), 0.01)

    def test_blend_renormalizes_and_events_need_one_winner(self):
        m = pd.DataFrame({"event_ticker": ["A", "A", "B", "B"], "won": [1, 0, 0, 0],
                          "market_p": [0.6, 0.4, 0.5, 0.5], "model_p": [0.3, 0.3, 0.2, 0.2]})
        blended = normalized_blend(m, "market_p", "model_p", 0.5)
        self.assertAlmostEqual(blended[:2].sum(), 1.0)
        self.assertEqual(list(complete_events(m).event_ticker.unique()), ["A"])


if pd is not None:
    class RecordsTrainingDates(BaseEstimator):
        """Predicts 1 for any row whose date it was trained on, so leaks are visible."""

        def fit(self, X, y):
            self.seen_ = set(X["date"])
            return self

        def predict(self, X):
            return X["date"].isin(self.seen_).astype(float).to_numpy()


@unittest.skipIf(pd is None, "analysis packages not installed")
class UncertaintyTests(unittest.TestCase):
    def test_out_of_fold_never_trains_on_held_out_or_embargoed_days(self):
        dates = pd.date_range("2024-01-01", "2025-12-31").strftime("%Y-%m-%d")
        frame = pd.DataFrame({"date": dates, "y": 0.0})
        self.assertEqual(out_of_fold(RecordsTrainingDates(), ["date"], "y", frame, folds=6).sum(), 0)

        # The days right after a held-out block are excluded too: their trailing features
        # contain the held-out observations.
        class Spy(RecordsTrainingDates):
            fits = []

            def fit(self, X, y):
                Spy.fits.append(pd.to_datetime(X["date"]))
                return super().fit(X, y)

        out_of_fold(Spy(), ["date"], "y", frame, folds=6)
        first_block_end = pd.Timestamp(np.array_split(pd.to_datetime(dates), 6)[0][-1])
        first_fit = Spy.fits[0]
        self.assertEqual(first_fit.min(), first_block_end + pd.Timedelta(days=EMBARGO_DAYS + 1))

    def test_sigma_model_learns_that_disagreement_means_larger_errors(self):
        rng = np.random.default_rng(1)
        spread = rng.uniform(0.5, 3.0, 6000)
        X = pd.DataFrame({"log_spread": np.log(spread), "station_id": "nyc_central_park"})
        error = rng.normal(0, spread)  # the true sigma equals the spread
        model = sigma_candidates(["log_spread"])["gamma_a0.01"].fit(X, error ** 2)
        grid = pd.DataFrame({"log_spread": np.log([1.0, 2.5]), "station_id": "nyc_central_park"})
        low, high = predict_sigma(model, grid)
        self.assertAlmostEqual(low, 1.0, delta=0.2)
        self.assertAlmostEqual(high, 2.5, delta=0.3)

    def test_calibrated_forecast_hits_its_intervals(self):
        rng = np.random.default_rng(2)
        n = 40000
        mu, sigma = pd.Series(rng.uniform(40, 90, n)), pd.Series(rng.uniform(1, 4, n))
        for nu, draw in ((None, rng.standard_normal(n)), (5, rng.standard_t(5, n) * np.sqrt(3 / 5))):
            scores = prob_scores(mu + sigma * draw, mu, sigma, nu)
            for level in (50, 80, 90):
                self.assertAlmostEqual(scores[f"coverage_{level}"], level / 100, delta=0.01)

    def test_whole_degree_probabilities_sum_to_one(self):
        degrees = np.arange(-300, 401)  # wide enough that the t tails are negligible
        for nu in (None, 8):
            self.assertAlmostEqual(integer_prob(degrees, 70.3, 2.2, nu).sum(), 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
