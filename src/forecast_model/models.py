"""Model families, tuned on the validation period and refit before the single test run."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


@dataclass
class FittedFamily:
    name: str                    # chosen candidate, e.g. "ridge_a100"
    val_mae: dict[str, float]    # every candidate's validation error
    sigma: dict[str, float]      # per-city residual std on validation (train-only fit)
    test_pred: pd.Series         # predictions after refitting on train + validation


def candidates(features: list[str]) -> dict[str, dict[str, Pipeline]]:
    numeric = [f for f in features if f != "station_id"]

    def scaled():
        return ColumnTransformer([
            ("num", make_pipeline(SimpleImputer(add_indicator=True), StandardScaler()), numeric),
            ("city", OneHotEncoder(handle_unknown="ignore"), ["station_id"]),
        ])

    def raw():
        # Gradient boosting handles missing values natively; no imputation needed.
        return ColumnTransformer([
            ("num", "passthrough", numeric),
            ("city", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["station_id"]),
        ])

    return {
        "ridge": {f"ridge_a{a}": make_pipeline(scaled(), Ridge(alpha=a)) for a in (1, 10, 100)},
        "boost": {f"hgb_lr{lr}_d{d}": make_pipeline(raw(), HistGradientBoostingRegressor(
                      learning_rate=lr, max_depth=d, max_iter=400, l2_regularization=1.0, random_state=0))
                  for lr in (0.03, 0.08) for d in (3, 6)},
    }


def fit_family(models: dict[str, Pipeline], columns: list[str], target: str,
               train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame) -> FittedFamily:
    def mae(model):
        return float(np.mean(np.abs(model.fit(train[columns], train[target]).predict(val[columns])
                                    - val[target])))

    val_mae = {name: mae(model) for name, model in models.items()}
    best = min(val_mae, key=val_mae.get)
    model = models[best]
    residual = model.fit(train[columns], train[target]).predict(val[columns]) - val[target]
    sigma = residual.groupby(val["station_id"]).std().to_dict()
    trainval = pd.concat([train, val])
    test_pred = pd.Series(model.fit(trainval[columns], trainval[target]).predict(test[columns]), test.index)
    return FittedFamily(best, val_mae, sigma, test_pred)
