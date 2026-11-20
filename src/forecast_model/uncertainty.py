"""Forecast uncertainty (sigma) for every day, instead of one per city."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import GammaRegressor
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from forecast_model.data import OBS_LAG_DAYS
from forecast_model.markets import MIN_SIGMA, std_cdf, std_ppf

FOLDS = 6
EMBARGO_DAYS = 30 + OBS_LAG_DAYS  # longest trailing window, plus the publication delay
TAILS = (None, 5, 8, 12)          # Student-t degrees of freedom; None is the normal


@dataclass
class FittedSigma:
    name: str                       # chosen sigma model, e.g. "gamma_a0.1"
    nu: float | None                # chosen tail: Student-t degrees of freedom, None = normal
    val_log_score: dict[str, float]  # every (model, tail) on validation, lower is better
    test_sigma: pd.Series           # per-day sigma for the test period, °F

    @property
    def label(self) -> str:
        return f"{self.name}, {tail_name(self.nu)}"


def tail_name(nu: float | None) -> str:
    return "normal" if nu is None else f"t{nu}"


def out_of_fold(model: Pipeline, columns: list[str], target: str, frame: pd.DataFrame,
                folds: int = FOLDS) -> pd.Series:
    """Predict every row with a copy of `model` fit without that row's time block, since
    errors on training days are too small. Rows just after the block are left out too,
    because their trailing features use its observations."""
    dates = pd.to_datetime(frame["date"])
    pred = pd.Series(np.nan, index=frame.index)
    for block in np.array_split(np.sort(dates.unique()), folds):
        held = dates.between(block[0], block[-1])
        embargo = (dates > block[-1]) & (dates <= block[-1] + pd.Timedelta(days=EMBARGO_DAYS))
        fit_rows = ~held & ~embargo
        fitted = clone(model).fit(frame.loc[fit_rows, columns], frame.loc[fit_rows, target])
        pred.loc[held] = fitted.predict(frame.loc[held, columns])
    return pred


def sigma_candidates(features: list[str]) -> dict[str, Pipeline]:
    """Models of the expected squared error. A gamma model with a log link fit to squared
    errors is the maximum-likelihood estimate of a normal distribution's variance."""
    def scaled():
        return ColumnTransformer([
            ("num", make_pipeline(SimpleImputer(add_indicator=True), StandardScaler()), features),
            ("city", OneHotEncoder(handle_unknown="ignore"), ["station_id"]),
        ])

    def raw():
        return ColumnTransformer([
            ("num", "passthrough", features),
            ("city", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["station_id"]),
        ])

    out = {f"gamma_a{a}": make_pipeline(scaled(), GammaRegressor(alpha=a, max_iter=1000))
           for a in (0.01, 0.1, 1.0)}
    out.update({f"hgb_gamma_d{d}": make_pipeline(raw(), HistGradientBoostingRegressor(
                    loss="gamma", learning_rate=0.03, max_depth=d, max_iter=200, min_samples_leaf=100,
                    l2_regularization=1.0, random_state=0))
                for d in (2, 3)})
    return out


def predict_sigma(model: Pipeline, X: pd.DataFrame) -> pd.Series:
    return pd.Series(np.maximum(np.sqrt(model.predict(X)), MIN_SIGMA), X.index)


def fit_sigma(mean_model: Pipeline, columns: list[str], target: str, features: list[str],
              train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame) -> FittedSigma:
    """Choose a sigma model and tail on validation, refit on train + validation, predict test."""
    history = pd.concat([train, val])
    mu = out_of_fold(mean_model, columns, target, history)
    obs = history[target]
    X, y = history[features + ["station_id"]], (obs - mu) ** 2 + 1e-6  # gamma needs y > 0
    is_train = history.index.isin(train.index)

    models = sigma_candidates(features)
    scores = {}
    for name, model in models.items():
        sigma = predict_sigma(model.fit(X[is_train], y[is_train]), X[~is_train])
        for nu in TAILS:
            scores[(name, nu)] = log_score(obs[~is_train], mu[~is_train], sigma, nu)
    name, nu = min(scores, key=scores.get)
    model = models[name].fit(X, y)
    return FittedSigma(name, nu, {f"{n}, {tail_name(t)}": s for (n, t), s in scores.items()},
                       predict_sigma(model, test[features + ["station_id"]]))


def integer_prob(obs, mu, sigma, nu: float | None = None):
    """Probability of the observed whole degree, the unit Kalshi settles on."""
    return std_cdf((obs + 0.5 - mu) / sigma, nu) - std_cdf((obs - 0.5 - mu) / sigma, nu)


def log_score(obs, mu, sigma, nu: float | None = None) -> float:
    return float(-np.mean(np.log(np.clip(integer_prob(obs, mu, sigma, nu), 1e-6, 1))))


def prob_scores(obs: pd.Series, mu: pd.Series, sigma: pd.Series, nu: float | None = None) -> dict:
    """Log score of the settled whole degree, and how often the truth lands in the forecast's
    central 50/80/90% intervals (a calibrated forecast hits each level on average)."""
    z = np.abs(obs - mu) / sigma
    out = {"log_score": log_score(obs, mu, sigma, nu), "mean_sigma": float(np.mean(sigma))}
    for level in (50, 80, 90):
        out[f"coverage_{level}"] = float(np.mean(z <= std_ppf(0.5 + level / 200, nu)))
    return {**out, "n": int(len(obs))}
