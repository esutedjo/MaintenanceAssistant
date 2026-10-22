"""Train RUL methods A and B on the historical engines and score them offline on the active fleet.
Run: uv run task train-rul  (needs uv run task download first)

The active-fleet true RUL comes from the NASA RUL files and is used here for scoring only; agents never see it."""

import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from data.db import load_pack
from data.fleet import SEED, build_fleet
from models.features import informative_sensors, fit_normaliser
from models.rul import HealthIndexRul, LightGbmRul, RulEstimate, train_health_index, train_lightgbm
from settings import ROOT

ARTIFACTS = ROOT / "models" / "artifacts" / "turbofan_cmapss"
HOLDOUT_SHARE = 0.2


def with_rul(cycles: pd.DataFrame) -> pd.DataFrame:
    """Run-to-failure engines: RUL at each cycle is cycles left until the last observed cycle."""
    return cycles.assign(rul=cycles.groupby("asset_id")["cycle"].transform("max") - cycles["cycle"])


def score(estimates: list[RulEstimate], truth: pd.Series, cap: float) -> dict:
    frame = pd.DataFrame([e.model_dump() for e in estimates]).set_index("asset_id")
    true = truth.reindex(frame.index).clip(upper=cap)
    error = frame["rul"] - true
    return {
        "engines": len(frame),
        "rmse": round(float(np.sqrt((error ** 2).mean())), 2),
        "mae": round(float(error.abs().mean()), 2),
        "interval_coverage": round(float(((true >= frame["lower"]) & (true <= frame["upper"])).mean()), 3),
        "mean_interval_width": round(float((frame["upper"] - frame["lower"]).mean()), 1),
        "not_fitted": int(frame["note"].notna().sum()),
    }


def divergence_share(a: list[RulEstimate], b: list[RulEstimate], pack: dict) -> float:
    limit = pack["thresholds"]["rul_divergence_cycles"]
    b_by_asset = {e.asset_id: e.rul for e in b}
    return round(float(np.mean([abs(e.rul - b_by_asset[e.asset_id]) > limit for e in a])), 3)


def main() -> int:
    pack = load_pack()
    cap = float(pack["thresholds"]["rul_cap_cycles"])
    assets, cycles = build_fleet(pack["thresholds"]["cycles_per_day"])
    historical = with_rul(cycles[cycles["asset_id"].str.contains("-H")])
    active = cycles[cycles["asset_id"].str.contains("-A")]

    units = np.sort(historical["asset_id"].unique())
    holdout = set(np.random.default_rng(SEED).choice(units, int(len(units) * HOLDOUT_SHARE), replace=False))
    train = historical[~historical["asset_id"].isin(holdout)]
    valid = historical[historical["asset_id"].isin(holdout)]

    stats = fit_normaliser(train, informative_sensors(pack))
    model_a = train_lightgbm(train, valid, pack, stats, SEED)
    model_b = train_health_index(train, pack, stats)

    truth = assets.set_index("asset_id")["true_rul_at_last_cycle"]
    a, b = model_a.predict(active), model_b.predict(active)
    report = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asset_pack_version": pack["version"],
        "train_engines": len(units) - len(holdout),
        "holdout_engines": len(holdout),
        "rounds": model_a.boosters["p50"].current_iteration(),
        "active_fleet": {"rul_a": score(a, truth, cap), "rul_b": score(b, truth, cap),
                         "a_b_divergence_share": divergence_share(a, b, pack)},
        "health_index": {"weights": {k: round(v, 4) for k, v in model_b.weights.items()},
                         "failure_thresholds": {k: round(v, 3) for k, v in model_b.thresholds.items()}},
    }
    model_a.save(ARTIFACTS)
    model_b.save(ARTIFACTS)
    (ARTIFACTS / "rul_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report["active_fleet"], indent=1))
    print(f"saved to {ARTIFACTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
