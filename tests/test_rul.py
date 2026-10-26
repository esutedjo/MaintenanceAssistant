import importlib.util
import sys

import numpy as np
import pandas as pd
import pytest

from data.db import load_pack
from models.features import common_trend_sensors, informative_sensors, normalise, rolling_features
from models.rul import MIN_HISTORY, HealthIndexRul, LightGbmRul, load_models
from models.train_rul import ARTIFACTS
from settings import ROOT

PACK = load_pack()


def _cycles(asset: str, n: int, sensors: dict[str, np.ndarray]) -> pd.DataFrame:
    return pd.DataFrame({"asset_id": asset, "cycle": np.arange(1, n + 1), "operating_regime": "R1", **sensors})


def test_missing_openmp_runtime_says_how_to_install_it(monkeypatch):
    # What a fresh Mac (no Homebrew libomp) or a minimal Linux (no libgomp1) hits when LightGBM loads.
    class NoOpenMP:
        def find_spec(self, name, path=None, target=None):
            if name == "lightgbm":
                raise OSError("libgomp.so.1: cannot open shared object file: No such file or directory")
            return None

    monkeypatch.delitem(sys.modules, "lightgbm", raising=False)
    monkeypatch.setattr(sys, "meta_path", [NoOpenMP(), *sys.meta_path])
    spec = importlib.util.spec_from_file_location("rul_without_openmp", ROOT / "models" / "rul.py")
    with pytest.raises(ImportError, match=r"brew install libomp.*sudo apt install libgomp1"):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_constant_sensors_dropped_and_common_trend_sensors_agree_across_modes():
    informative = informative_sensors(PACK)
    assert not {"s_1", "s_5", "s_6", "s_10", "s_16", "s_18", "s_19"} & set(informative)
    common = common_trend_sensors(PACK)
    assert common["s_3"] == 1 and "s_12" not in common and "s_7" not in common


def test_normalise_rejects_unseen_regime_and_coerces_strings():
    stats = {"R1": {"s_2": [10.0, 2.0]}}
    frame = _cycles("X", 3, {"s_2": np.array(["10", "12", "14"])})
    assert normalise(frame, ["s_2"], stats)["s_2"].tolist() == [0.0, 1.0, 2.0]
    with pytest.raises(ValueError, match="operating regimes"):
        normalise(frame.assign(operating_regime="R9"), ["s_2"], stats)


def test_rolling_slope_matches_linear_trend():
    frame = _cycles("X", 40, {"s_2": 0.5 * np.arange(40.0)})
    features = rolling_features(frame, ["s_2"])
    assert features["s_2_slope10"].iloc[-1] == pytest.approx(0.5)
    assert features["s_2_slope30"].iloc[-1] == pytest.approx(0.5)


def _health_index_model() -> HealthIndexRul:
    stats = {"R1": {"s_3": [0.0, 1.0]}}
    return HealthIndexRul(weights={"s_3": 1.0}, intercept=0.0, stats=stats,
                          thresholds={"p10": 0.9, "p50": 1.0, "p90": 1.1}, cap=125.0, baseline=0.0, onset=0.1)


def test_health_index_extrapolates_exponential_trend_to_threshold():
    a, b, fail = 0.01, 0.03, 160
    t = np.arange(1, 141, dtype=float)
    signal = a * (np.exp(b * t) - 1) / (a * (np.exp(b * fail) - 1))
    estimate = _health_index_model().predict(_cycles("X", 140, {"s_3": signal}))[0]
    assert estimate.rul == pytest.approx(20, abs=2)
    assert estimate.lower <= estimate.rul <= estimate.upper
    assert estimate.note is None


def test_health_index_returns_cap_with_note_for_short_or_flat_history():
    model = _health_index_model()
    short = model.predict(_cycles("S", MIN_HISTORY - 1, {"s_3": np.zeros(MIN_HISTORY - 1)}))[0]
    flat = model.predict(_cycles("F", 80, {"s_3": np.zeros(80)}))[0]
    assert short.rul == flat.rul == 125.0 and short.at_cap
    assert "history" in short.note and "healthy" in flat.note


@pytest.mark.skipif(not (ARTIFACTS / "rul_a.json").exists(), reason="run uv run task train-rul first")
def test_saved_models_predict_within_cap_and_ordered_interval():
    model_a, model_b = load_models(ARTIFACTS)
    assert isinstance(model_a, LightGbmRul)
    rng = np.random.default_rng(0)
    regime, stats = next(iter(model_a.stats.items()))
    sensors = {s: stats[s][0] + rng.normal(0, stats[s][1], 60) for s in model_a.sensors}
    frame = _cycles("FD001-A999", 60, sensors).assign(operating_regime=regime)
    for estimate in (*model_a.predict(frame), *model_b.predict(frame)):
        assert 0 <= estimate.lower <= estimate.rul <= estimate.upper <= 125
