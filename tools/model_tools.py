"""Model tools for the prognostics and diagnostics agents: RUL methods A and B, SHAP drivers of A, and the
fault-mode signature match. Each returns Evidence (kind "model") plus the typed finding that cites it."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from casefile.schemas import AnomalyFinding, Driver, Evidence, EvidenceLedger, FaultModeFinding, RulFinding
from models.fault_mode import SignatureMatcher
from models.rul import HealthIndexRul, LightGbmRul, RulEstimate
from settings import ROOT

TOP_DRIVERS = 5
MIN_DRIVER_CYCLES = 0.5


@dataclass
class Models:
    rul_a: LightGbmRul
    rul_b: HealthIndexRul
    fault_mode: SignatureMatcher
    report: dict
    fleet_curve: dict


@lru_cache(maxsize=4)
def _load(directory: str, pack_json: str) -> Models:
    path, pack = Path(directory), json.loads(pack_json)
    rul_a = LightGbmRul.load(path)
    report = json.loads((path / "rul_report.json").read_text(encoding="utf-8"))
    curve_path = path / "fleet_curve.json"
    fleet_curve = json.loads(curve_path.read_text(encoding="utf-8")) if curve_path.exists() else {"points": []}
    return Models(rul_a, HealthIndexRul.load(path), SignatureMatcher.load(path, pack, rul_a.stats), report, fleet_curve)


def load_models(pack: dict) -> Models:
    return _load(str(ROOT / pack["models"]["directory"]), json.dumps(pack, sort_keys=True, default=str))


def _finding(estimate: RulEstimate, evidence: Evidence) -> RulFinding:
    return RulFinding(method=estimate.method, rul=estimate.rul, lower=estimate.lower, upper=estimate.upper,
                      at_cap=estimate.at_cap, note=estimate.note, evidence_id=evidence.id)


def _single(estimates: list, asset_id: str):
    if len(estimates) != 1 or estimates[0].asset_id != asset_id:
        raise ValueError(f"expected one estimate for {asset_id}, got {[e.asset_id for e in estimates]}")
    return estimates[0]


class ModelTools:
    def __init__(self, pack: dict, ledger: EvidenceLedger):
        self.pack, self.ledger = pack, ledger
        self.models = load_models(pack)
        self.cap = float(pack["thresholds"]["rul_cap_cycles"])

    def _source(self, name: str) -> str:
        return f"model:{name}@{self.pack['asset_type']}/{self.pack['version']}"

    def rul_ml(self, asset_id: str, cycles: pd.DataFrame) -> RulFinding:
        estimate = _single(self.models.rul_a.predict(cycles), asset_id)
        metrics = self.models.report["active_fleet"]["rul_a"]
        content = {**estimate.model_dump(), "cap": self.cap, "interval": "10th-90th percentile quantile models",
                   "offline_metrics": {k: metrics[k] for k in ("rmse", "mae", "interval_coverage")}}
        summary = (f"RUL A (LightGBM): {estimate.rul:.0f} cycles, 10th-90th {estimate.lower:.0f}-{estimate.upper:.0f}"
                   f" at cycle {estimate.last_cycle}" + (" (at cap)" if estimate.at_cap else ""))
        return _finding(estimate, self.ledger.add("model", self._source("rul_a"), summary, content))

    def rul_trend(self, asset_id: str, cycles: pd.DataFrame) -> RulFinding:
        model = self.models.rul_b
        estimate = _single(model.predict(cycles), asset_id)
        level = float(model.degradation(cycles)["smoothed"].iloc[-1])
        metrics = self.models.report["active_fleet"]["rul_b"]
        content = {**estimate.model_dump(), "cap": self.cap, "health_index": round(level, 3),
                   "healthy_band_upper": round(model.onset, 3),
                   "failure_thresholds": {k: round(v, 3) for k, v in model.thresholds.items()},
                   "degradation_detected": bool(level >= model.onset),
                   "offline_metrics": {k: metrics[k] for k in ("rmse", "mae")}}
        spread = "" if estimate.at_cap else f" (lower {estimate.lower:.0f}, upper {estimate.upper:.0f})"
        summary = (f"RUL B (health-index trend): {estimate.rul:.0f} cycles{spread}, health index {level:.2f} "
                   f"(healthy band up to {model.onset:.2f})" + (f"; {estimate.note}" if estimate.note else ""))
        return _finding(estimate, self.ledger.add("model", self._source("rul_b"), summary, content))

    def drivers(self, asset_id: str, cycles: pd.DataFrame) -> list[Driver]:
        """Exact TreeSHAP contributions of RUL A's point model at the last cycle, summed per sensor.
        Negative values pull the RUL estimate down."""
        model = self.models.rul_a
        frame = model.feature_frame(cycles)
        last = frame[frame["asset_id"] == asset_id].tail(1)[model.features]
        contrib = model.boosters["p50"].predict(last, pred_contrib=True)[0]
        base, values = float(contrib[-1]), dict(zip(model.features, contrib[:-1]))
        by_sensor: dict[str, float] = {}
        for feature, value in values.items():
            key = feature.split("_mean")[0].split("_slope")[0]
            by_sensor[key] = by_sensor.get(key, 0.0) + float(value)
        age = by_sensor.pop("cycle", 0.0)
        sensors = self.pack["sensors"]
        by_component: dict[str, float] = {}
        for sensor, value in by_sensor.items():
            component = sensors[sensor]["component"]
            by_component[component] = by_component.get(component, 0.0) + value
        ranked = [(s, v) for s, v in sorted(by_sensor.items(), key=lambda kv: kv[1]) if v <= -MIN_DRIVER_CYCLES]
        ranked = ranked[:TOP_DRIVERS]
        content = {"asset_id": asset_id, "method": "LightGBM TreeSHAP (pred_contrib), RUL A point model",
                   "base_value": round(base, 2), "raw_prediction": round(base + age + sum(by_sensor.values()), 2),
                   "age_contribution": round(age, 2),
                   "by_sensor": {s: round(v, 2) for s, v in sorted(by_sensor.items(), key=lambda kv: kv[1])},
                   "by_component": {c: round(v, 2) for c, v in sorted(by_component.items(), key=lambda kv: kv[1])}}
        if ranked:
            text = ", ".join(f"{sensors[s]['name']} ({sensors[s]['component']}) {v:+.1f}" for s, v in ranked)
            summary = f"Sensors pulling RUL A down, in cycles: {text}"
        else:
            summary = f"No sensor pulls RUL A down by {MIN_DRIVER_CYCLES} cycles or more"
        evidence = self.ledger.add("model", self._source("shap_rul_a"), summary, content)
        return [Driver(sensor=s, component=sensors[s]["component"], contribution=round(v, 2), evidence_id=evidence.id)
                for s, v in ranked]

    def anomaly(self, asset_id: str, cycles: pd.DataFrame) -> AnomalyFinding:
        """Health index at the last cycle minus the active-fleet median at that cycle, in fleet standard deviations."""
        model = self.models.rul_b
        series = model.degradation(cycles)
        cycle = int(series["cycle"].iloc[-1])
        level = float(series["smoothed"].iloc[-1])
        point = min(self.models.fleet_curve.get("points") or [{"cycle": cycle, "median": 0.0, "std": 0.0, "n": 0}],
                    key=lambda p: abs(p["cycle"] - cycle))
        std = max(float(point.get("std") or 0.0), 1e-6)
        residual = (level - float(point["median"])) / std
        limit = float(self.pack["thresholds"]["anomaly_sigma"])
        flagged = residual >= limit
        content = {"asset_id": asset_id, "cycle": cycle, "health_index": round(level, 3),
                   "fleet_cycle": point["cycle"], "fleet_median": round(float(point["median"]), 3),
                   "fleet_std": round(std, 3), "fleet_n": point.get("n"), "residual_sigma": round(residual, 2),
                   "threshold_sigma": limit, "flagged": flagged}
        if flagged:
            summary = (f"Anomaly: health index {level:.2f} is {residual:.1f} sd above the fleet median "
                       f"{point['median']:.2f} at cycle {cycle}")
        else:
            summary = (f"Health index {level:.2f} is {residual:+.1f} sd from the fleet median "
                       f"{point['median']:.2f} at cycle {cycle} (flag at +{limit:.0f} sd)")
        evidence = self.ledger.add("model", self._source("anomaly"), summary, content)
        return AnomalyFinding(cycle=cycle, health_index=round(level, 3), fleet_median=round(float(point["median"]), 3),
                              residual_sigma=round(residual, 2), flagged=flagged, evidence_id=evidence.id)

    def degradation_detected(self, cycles: pd.DataFrame) -> bool:
        """Health index (method B) above the healthy fleet band."""
        model = self.models.rul_b
        return bool(model.degradation(cycles)["smoothed"].iloc[-1] >= model.onset)

    def fault_modes(self, asset_id: str, cycles: pd.DataFrame, variant: str) -> list[FaultModeFinding]:
        match = _single(self.models.fault_mode.match(cycles, variant), asset_id)
        degradation_detected = self.degradation_detected(cycles)
        sensors = self.pack["sensors"]
        content = {"asset_id": asset_id, "variant": variant, "candidates": self.pack["variants"][variant],
                   "probabilities": match.probabilities, "shift_sd": match.shifts,
                   "signatures": match.signatures, "temperature": self.models.fault_mode.temperature,
                   "degradation_detected": degradation_detected, "note": match.note}
        ranked = sorted(match.probabilities.items(), key=lambda kv: -kv[1])
        moved = ", ".join(f"{sensors[s]['name']} {v:+.1f} sd" for s, v in match.shifts.items())
        summary = "Fault-mode match: " + ", ".join(f"{m} {p:.2f}" for m, p in ranked)
        summary += f" on {moved}" if moved else f" ({match.note})"
        if not degradation_detected:
            summary += "; health index still in healthy band, signature is weak"
        evidence = self.ledger.add("model", self._source("fault_mode"), summary, content)
        return [FaultModeFinding(failure_mode=m, probability=p, evidence_id=evidence.id) for m, p in ranked]
