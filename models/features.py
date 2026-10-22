"""Feature pipeline shared by training and inference. Sensor choices come from the asset pack."""

import numpy as np
import pandas as pd

WINDOWS = (10, 30)


def informative_sensors(pack: dict) -> list[str]:
    """Sensors that move under at least one failure mode (the constant ones are dropped)."""
    return [s for s, spec in pack["sensors"].items() if set(spec["direction"].values()) != {"none"}]


def common_trend_sensors(pack: dict) -> dict[str, int]:
    """Sensors that move the same way under every failure mode: +1 rises, -1 falls."""
    signs = {}
    for sensor, spec in pack["sensors"].items():
        directions = set(spec["direction"].values())
        if directions in ({"up"}, {"down"}):
            signs[sensor] = 1 if directions == {"up"} else -1
    return signs


def fit_normaliser(frame: pd.DataFrame, sensors: list[str]) -> dict[str, dict[str, list[float]]]:
    """Mean and standard deviation per operating regime and sensor."""
    stats = {}
    for regime, group in frame.groupby("operating_regime"):
        values = group[sensors].astype(float)
        std = values.std(ddof=0).replace(0.0, 1.0)
        stats[str(regime)] = {s: [float(values[s].mean()), float(std[s])] for s in sensors}
    return stats


def normalise(frame: pd.DataFrame, sensors: list[str], stats: dict) -> pd.DataFrame:
    """Sort by asset and cycle, coerce types (Zetaris returns strings) and z-score sensors per regime."""
    out = frame.copy()
    out["cycle"] = out["cycle"].astype(int)
    out = out.sort_values(["asset_id", "cycle"]).reset_index(drop=True)
    out[sensors] = out[sensors].astype(float)
    unknown = set(out["operating_regime"].astype(str)) - set(stats)
    if unknown:
        raise ValueError(f"operating regimes not seen in training: {sorted(unknown)}")
    for regime, columns in stats.items():
        mask = out["operating_regime"].astype(str) == regime
        mean = np.array([columns[s][0] for s in sensors])
        std = np.array([columns[s][1] for s in sensors])
        out.loc[mask, sensors] = (out.loc[mask, sensors].to_numpy() - mean) / std
    return out


def _rolling_slope(values: pd.Series, window: int) -> pd.Series:
    t = pd.Series(np.arange(len(values), dtype=float), index=values.index)

    def mean(v: pd.Series) -> pd.Series:
        return v.rolling(window, min_periods=3).mean()

    variance = mean(t * t) - mean(t) ** 2
    return ((mean(t * values) - mean(t) * mean(values)) / variance).fillna(0.0)


def rolling_features(normalised: pd.DataFrame, sensors: list[str]) -> pd.DataFrame:
    """One row per input row: cycle, sensor level, rolling mean and slope over each window."""
    groups = normalised.groupby("asset_id", sort=False)
    columns = {"cycle": normalised["cycle"].astype(float)}
    for s in sensors:
        columns[s] = normalised[s]
        for w in WINDOWS:
            columns[f"{s}_mean{w}"] = groups[s].transform(lambda v, w=w: v.rolling(w, min_periods=1).mean())
            columns[f"{s}_slope{w}"] = groups[s].transform(lambda v, w=w: _rolling_slope(v, w))
    return pd.DataFrame(columns, index=normalised.index)


def feature_names(sensors: list[str]) -> list[str]:
    names = ["cycle"]
    for s in sensors:
        names += [s, *(f"{s}_{kind}{w}" for w in WINDOWS for kind in ("mean", "slope"))]
    return names
