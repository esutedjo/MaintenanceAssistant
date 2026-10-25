"""Two independent remaining-useful-life methods.

A: LightGBM on rolling sensor features (point estimate plus 10th/90th percentile models).
B: health index (linear combination of sensors that degrade the same way under every failure mode),
   smoothed and extrapolated with an exponential trend to the failure threshold seen in training.
"""

import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd
from pydantic import BaseModel
from scipy.optimize import OptimizeWarning, curve_fit

from models.features import common_trend_sensors, feature_names, informative_sensors, normalise, rolling_features

QUANTILES = {"p10": 0.1, "p90": 0.9}
MIN_HISTORY = 20
SMOOTH_WINDOW = 10
BASELINE_CYCLES = 20


class RulEstimate(BaseModel):
    method: Literal["lightgbm", "health_index"]
    asset_id: str
    last_cycle: int
    rul: float
    lower: float
    upper: float
    at_cap: bool
    note: str | None = None


def _clip(value: float, cap: float) -> float:
    return round(float(min(max(value, 0.0), cap)), 1)


@dataclass
class LightGbmRul:
    boosters: dict[str, lgb.Booster]
    sensors: list[str]
    stats: dict
    cap: float
    widen_lower: float = 0.0    # conformal margins (cycles) per side; models/calibrate_rul.py
    widen_upper: float = 0.0

    @property
    def features(self) -> list[str]:
        return feature_names(self.sensors)

    def feature_frame(self, cycles: pd.DataFrame) -> pd.DataFrame:
        normalised = normalise(cycles, self.sensors, self.stats)
        return normalised[["asset_id", "cycle"]].join(rolling_features(normalised, self.sensors)[self.features[1:]])

    def predict(self, cycles: pd.DataFrame) -> list[RulEstimate]:
        last = self.feature_frame(cycles).groupby("asset_id").tail(1)
        x = last[self.features]
        raw = {name: booster.predict(x) for name, booster in self.boosters.items()}
        estimates = []
        for i, (asset, cycle) in enumerate(zip(last["asset_id"], last["cycle"])):
            point = _clip(raw["p50"][i], self.cap)
            lower, upper = sorted((_clip(raw["p10"][i] - self.widen_lower, self.cap),
                                   _clip(raw["p90"][i] + self.widen_upper, self.cap)))
            estimates.append(RulEstimate(
                method="lightgbm", asset_id=asset, last_cycle=int(cycle), rul=point,
                lower=min(lower, point), upper=max(upper, point), at_cap=point >= self.cap,
            ))
        return estimates

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, booster in self.boosters.items():
            booster.save_model(str(directory / f"rul_a_{name}.txt"))
        meta = {"sensors": self.sensors, "stats": self.stats, "cap": self.cap,
                "widen_lower": self.widen_lower, "widen_upper": self.widen_upper}
        (directory / "rul_a.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "LightGbmRul":
        meta = json.loads((directory / "rul_a.json").read_text(encoding="utf-8"))
        boosters = {n: lgb.Booster(model_file=str(directory / f"rul_a_{n}.txt")) for n in ("p50", *QUANTILES)}
        return cls(boosters=boosters, **meta)


def train_lightgbm(train: pd.DataFrame, valid: pd.DataFrame, pack: dict, stats: dict, seed: int) -> LightGbmRul:
    """train/valid: cycles with an `rul` column. Early stopping on the point model sets the round count."""
    cap = float(pack["thresholds"]["rul_cap_cycles"])
    model = LightGbmRul(boosters={}, sensors=informative_sensors(pack), stats=stats, cap=cap)
    x_train, x_valid = (model.feature_frame(f)[model.features] for f in (train, valid))
    y_train, y_valid = (f.sort_values(["asset_id", "cycle"])["rul"].clip(upper=cap).to_numpy() for f in (train, valid))
    params = dict(learning_rate=0.03, num_leaves=31, min_child_samples=50, subsample=0.8, subsample_freq=1,
                  colsample_bytree=0.8, random_state=seed, deterministic=True, verbose=-1)
    point = lgb.LGBMRegressor(n_estimators=3000, **params)
    point.fit(x_train, y_train, eval_X=(x_valid,), eval_y=(y_valid,), callbacks=[lgb.early_stopping(100, verbose=False)])
    rounds = point.best_iteration_ or 3000
    model.boosters["p50"] = point.booster_
    for name, alpha in QUANTILES.items():
        quantile = lgb.LGBMRegressor(objective="quantile", alpha=alpha, n_estimators=rounds, **params)
        model.boosters[name] = quantile.fit(x_train, y_train).booster_
    return model


def relative_to_own_start(normalised: pd.DataFrame, sensors: list[str]) -> pd.DataFrame:
    """Subtract each engine's mean over its first BASELINE_CYCLES cycles: engines start with different wear."""
    out = normalised.copy()
    start = out.groupby("asset_id")[sensors].transform(lambda v: v.iloc[:BASELINE_CYCLES].mean())
    out[sensors] = out[sensors] - start
    return out


def _exp_trend(t, d0, a, b):
    return d0 + a * (np.exp(b * t) - 1.0)


@dataclass
class HealthIndexRul:
    weights: dict[str, float]
    intercept: float
    stats: dict
    thresholds: dict[str, float]
    cap: float
    baseline: float = 0.0
    onset: float = 0.0

    def degradation(self, cycles: pd.DataFrame) -> pd.DataFrame:
        """Per cycle: raw and smoothed degradation (0 healthy, about 1 at failure)."""
        sensors = list(self.weights)
        relative = relative_to_own_start(normalise(cycles, sensors, self.stats), sensors)
        raw = relative[sensors].to_numpy() @ np.array(list(self.weights.values())) + self.intercept
        out = relative[["asset_id", "cycle"]].assign(degradation=raw)
        out["smoothed"] = out.groupby("asset_id")["degradation"].transform(
            lambda v: v.rolling(SMOOTH_WINDOW, min_periods=1).mean())
        return out

    def fit_trend(self, cycle: np.ndarray, degradation: np.ndarray) -> tuple[float, float] | None:
        """Least-squares exponential trend anchored at the healthy baseline; None if it does not converge."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", OptimizeWarning)
                (a, b), _ = curve_fit(lambda t, a, b: _exp_trend(t, self.baseline, a, b), cycle, degradation,
                                      p0=(0.01, 0.02), bounds=([1e-8, 1e-3], [10.0, 0.2]), maxfev=10000)
        except (RuntimeError, ValueError):
            return None
        return float(a), float(b)

    def _extrapolate(self, cycle: np.ndarray, raw: np.ndarray, smoothed: np.ndarray) -> tuple[dict[str, float], str | None]:
        if len(cycle) < MIN_HISTORY:
            return {k: self.cap for k in self.thresholds}, f"only {len(cycle)} cycles of history; trend not fitted"
        if smoothed[-1] < self.onset:
            return {k: self.cap for k in self.thresholds}, "health index within healthy fleet band; no degradation trend yet"
        fitted = self.fit_trend(cycle, raw)
        if fitted is None:
            return {k: self.cap for k in self.thresholds}, "exponential trend fit did not converge"
        a, b = fitted
        last = float(cycle[-1])
        now = _exp_trend(last, self.baseline, a, b)
        rul = {}
        for name, threshold in self.thresholds.items():
            if now >= threshold:
                rul[name] = 0.0
            else:
                rul[name] = math.log((threshold - self.baseline) / a + 1.0) / b - last
        return {k: _clip(v, self.cap) for k, v in rul.items()}, None

    def predict(self, cycles: pd.DataFrame) -> list[RulEstimate]:
        estimates = []
        for asset, group in self.degradation(cycles).groupby("asset_id", sort=False):
            rul, note = self._extrapolate(group["cycle"].to_numpy(float), group["degradation"].to_numpy(),
                                          group["smoothed"].to_numpy())
            lower, upper = sorted((rul["p10"], rul["p90"]))
            point = rul["p50"]
            estimates.append(RulEstimate(
                method="health_index", asset_id=asset, last_cycle=int(group["cycle"].iloc[-1]), rul=point,
                lower=min(lower, point), upper=max(upper, point), at_cap=point >= self.cap, note=note,
            ))
        return estimates

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        meta = {"weights": self.weights, "intercept": self.intercept, "stats": self.stats,
                "thresholds": self.thresholds, "cap": self.cap, "baseline": self.baseline, "onset": self.onset}
        (directory / "rul_b.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "HealthIndexRul":
        return cls(**json.loads((directory / "rul_b.json").read_text(encoding="utf-8")))


def train_health_index(train: pd.DataFrame, pack: dict, stats: dict) -> HealthIndexRul:
    """Fit degradation = 1 - min(rul, cap) / cap by least squares. The healthy baseline and onset are the median
    and 95th percentile of smoothed degradation while RUL is still above the cap. Failure thresholds are the
    10th/50th/90th percentile of the fitted trend at the last cycle of each training engine."""
    cap = float(pack["thresholds"]["rul_cap_cycles"])
    sensors = list(common_trend_sensors(pack))
    normalised = relative_to_own_start(normalise(train, sensors, stats), sensors)
    target = 1.0 - normalised["rul"].clip(upper=cap) / cap
    design = np.column_stack([normalised[sensors].to_numpy(), np.ones(len(normalised))])
    coef, *_ = np.linalg.lstsq(design, target.to_numpy(), rcond=None)
    model = HealthIndexRul(weights=dict(zip(sensors, map(float, coef[:-1]))), intercept=float(coef[-1]),
                           stats=stats, thresholds={}, cap=cap)
    degradation = model.degradation(train)
    healthy = degradation.loc[normalised["rul"].to_numpy() > cap, "smoothed"]
    model.baseline, model.onset = float(healthy.median()), float(healthy.quantile(0.95))
    at_failure = []
    for _, group in degradation.groupby("asset_id"):
        cycle = group["cycle"].to_numpy(float)
        fitted = model.fit_trend(cycle, group["degradation"].to_numpy())
        if fitted is not None:
            at_failure.append(_exp_trend(cycle[-1], model.baseline, *fitted))
    levels = pd.Series(at_failure)
    model.thresholds = {name: float(levels.quantile(q)) for name, q in (("p10", 0.1), ("p50", 0.5), ("p90", 0.9))}
    return model


def load_models(directory: Path) -> tuple[LightGbmRul, HealthIndexRul]:
    return LightGbmRul.load(directory), HealthIndexRul.load(directory)
