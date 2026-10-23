"""Calibrate the fault-mode matcher's softmax temperature on historical FD003 engines (run to failure, so the proxy
label is reliable), cut at 30-100% of life so early, weak signatures keep their uncertainty. Scores the active
fleet offline against proxy labels. Run: uv run task train-fault-mode (needs uv run task download)."""

import json
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from data.db import load_pack
from data.fleet import build_fleet
from models.fault_mode import SignatureMatcher
from models.rul import load_models
from models.train_rul import ARTIFACTS

LIFE_FRACTIONS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
AMBIGUOUS = 0.15


def degraded(model_b, cycles: pd.DataFrame) -> set[str]:
    last = model_b.degradation(cycles).groupby("asset_id")["smoothed"].last()
    return set(last[last >= model_b.onset].index)


def truncations(cycles: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for asset, group in cycles.groupby("asset_id"):
        for fraction in LIFE_FRACTIONS:
            end = int(round(len(group) * fraction))
            parts.append(group.iloc[:end].assign(asset_id=f"{asset}@{fraction}"))
    return pd.concat(parts, ignore_index=True)


def evaluate(matcher, cycles, labels, variant) -> tuple[pd.DataFrame, dict]:
    rows = [{"asset_id": m.asset_id, "label": labels[m.asset_id.split("@")[0]], **m.probabilities}
            for m in matcher.match(cycles, variant)]
    frame = pd.DataFrame(rows)
    modes = matcher.pack["variants"][variant]
    p_true = frame.apply(lambda r: r[r["label"]], axis=1).clip(1e-6, 1)
    top = frame[modes].idxmax(axis=1)
    gap = frame[modes].max(axis=1) - frame[modes].min(axis=1)
    return frame, {"cases": len(frame), "accuracy": round(float((top == frame["label"]).mean()), 3),
                   "log_loss": round(float(-np.log(p_true).mean()), 3),
                   "ambiguous_share": round(float((gap < AMBIGUOUS).mean()), 3)}


def main() -> int:
    pack = load_pack()
    assets, cycles = build_fleet(pack["thresholds"]["cycles_per_day"])
    labels = assets.set_index("asset_id")["proxy_fault_mode"]
    model_a, model_b = load_models(ARTIFACTS)
    fd003 = cycles[cycles["asset_id"].str.startswith("FD003")]

    historical = truncations(fd003[fd003["asset_id"].str.contains("-H")])
    matcher = SignatureMatcher(pack, model_a.stats)

    def loss(log_t: float) -> float:
        matcher.temperature = float(np.exp(log_t))
        return evaluate(matcher, historical, labels, "FD003")[1]["log_loss"]

    best = minimize_scalar(loss, bounds=(np.log(0.01), np.log(10.0)), method="bounded")
    matcher.temperature = round(float(np.exp(best.x)), 4)
    _, calibration = evaluate(matcher, historical, labels, "FD003")
    _, calibration_degraded = evaluate(
        matcher, historical[historical["asset_id"].isin(degraded(model_b, historical))], labels, "FD003")

    active = fd003[fd003["asset_id"].str.contains("-A")]
    active_degraded = active[active["asset_id"].isin(degraded(model_b, active))]
    _, all_active = evaluate(matcher, active, labels, "FD003")
    _, degraded_active = evaluate(matcher, active_degraded, labels, "FD003")

    matcher.save(ARTIFACTS)
    report = {"temperature": matcher.temperature, "calibration_historical_fd003": calibration,
              "calibration_historical_fd003_degraded": calibration_degraded,
              "active_fd003_all": all_active, "active_fd003_degraded": degraded_active}
    (ARTIFACTS / "fault_mode_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
