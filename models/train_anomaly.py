"""Fleet health-index curve used by the anomaly flag. Built from the committed snapshot (active engines), so it
does not need the NASA download. Run: uv run task train-anomaly."""

import json
import sys

import pandas as pd

from data.db import load_pack
from models.rul import HealthIndexRul
from models.train_rul import ARTIFACTS
from settings import ROOT
from tools.data_access import SNAPSHOT_DIR

MIN_ENGINES = 8


def fleet_curve(degradation: pd.DataFrame) -> dict:
    rows = []
    for cycle, group in degradation.groupby("cycle"):
        values = group["smoothed"]
        if len(values) < MIN_ENGINES:
            continue
        rows.append({
            "cycle": int(cycle), "n": int(len(values)),
            "median": round(float(values.median()), 4),
            "std": round(float(values.std(ddof=0) or 0.0), 4),
            "p10": round(float(values.quantile(0.1)), 4),
            "p90": round(float(values.quantile(0.9)), 4),
        })
    return {"min_engines": MIN_ENGINES, "points": rows}


def main() -> int:
    pack = load_pack()
    cycles = pd.read_parquet(SNAPSHOT_DIR / "cycles.parquet")
    model = HealthIndexRul.load(ARTIFACTS)
    curve = fleet_curve(model.degradation(cycles))
    path = ARTIFACTS / "fleet_curve.json"
    path.write_text(json.dumps(curve, indent=1), encoding="utf-8")
    print(f"{len(curve['points'])} cycle points from {cycles.asset_id.nunique()} active engines -> {path.relative_to(ROOT)}")
    print(f"asset pack anomaly_sigma={pack['thresholds']['anomaly_sigma']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
