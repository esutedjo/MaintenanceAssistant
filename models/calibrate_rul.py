"""Calibrate the 10th-90th range of RUL method A to its nominal 80% coverage (conformalised quantile regression, one
margin per side), without retraining. The 40 held-out historical engines are cut every few cycles. The lower margin
is the 90th percentile of how far the raw 10th percentile sits above the true RUL; the upper margin likewise for the
90th percentile. One margin per side because the misses are lopsided: most sit at the 125-cycle cap above the range,
and the safety-relevant ones sit below the lower bound. The margins are stored in rul_a.json and applied in
LightGbmRul.predict. The held-out engines also set the point model's early stopping.
Run: uv run task calibrate-rul  (needs uv run task download first)"""

import json
import sys

import numpy as np

from data.db import load_pack
from data.fleet import SEED, build_fleet
from models.rul import LightGbmRul
from models.train_rul import ARTIFACTS, HOLDOUT_SHARE, score, with_rul
from settings import ROOT

TARGET = 0.80
STEP = 5                     # cycles between cut points
MIN_HISTORY = 30             # A reads the last 30 cycles


def holdout_scores(model: LightGbmRul, valid) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Raw p10, p90 and capped true RUL at every cut point of the held-out engines (features are causal)."""
    frame = model.feature_frame(valid).merge(valid[["asset_id", "cycle", "rul"]], on=["asset_id", "cycle"])
    cuts = frame[(frame["cycle"] >= MIN_HISTORY) & (frame["cycle"] % STEP == 0)]
    x = cuts[model.features]
    lo, hi = model.boosters["p10"].predict(x), model.boosters["p90"].predict(x)
    truth = cuts["rul"].clip(upper=model.cap).to_numpy()
    return np.clip(lo, 0, model.cap), np.clip(hi, 0, model.cap), truth


def main() -> int:
    pack = load_pack()
    assets, cycles = build_fleet(pack["thresholds"]["cycles_per_day"])
    historical = with_rul(cycles[cycles["asset_id"].str.contains("-H")])
    units = np.sort(historical["asset_id"].unique())
    holdout = set(np.random.default_rng(SEED).choice(units, int(len(units) * HOLDOUT_SHARE), replace=False))
    valid = historical[historical["asset_id"].isin(holdout)]

    model = LightGbmRul.load(ARTIFACTS)
    model.widen_lower = model.widen_upper = 0.0
    lo, hi, truth = holdout_scores(model, valid)
    n = len(truth)
    level = min(1.0, (1 - (1 - TARGET) / 2) * (1 + 1 / n))
    widen_lower = max(round(float(np.quantile(lo - truth, level)), 1), 0.0)
    widen_upper = max(round(float(np.quantile(truth - hi, level)), 1), 0.0)

    active = cycles[cycles["asset_id"].str.contains("-A")]
    truth_active = assets.set_index("asset_id")["true_rul_at_last_cycle"]
    before = score(model.predict(active), truth_active, model.cap)
    model.widen_lower, model.widen_upper = widen_lower, widen_upper
    after = score(model.predict(active), truth_active, model.cap)
    calibration = {
        "method": "conformalised quantile regression on held-out engines, one margin per side",
        "target_coverage": TARGET, "holdout_engines": len(holdout), "cut_points": n,
        "widen_lower_cycles": widen_lower, "widen_upper_cycles": widen_upper,
        "holdout_coverage_before": round(float(((truth >= lo) & (truth <= hi)).mean()), 3),
        "holdout_coverage_after": round(float(((truth >= lo - widen_lower) & (truth <= hi + widen_upper)).mean()), 3),
        "active_fleet_before": before, "active_fleet_after": after,
    }
    model.save(ARTIFACTS)
    report_path = ARTIFACTS / "rul_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["calibration"] = calibration
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(calibration, indent=1))
    print(f"saved to {ARTIFACTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
