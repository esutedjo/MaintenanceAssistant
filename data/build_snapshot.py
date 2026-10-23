"""Build data/snapshot: the Parquet extracts sample mode runs on. Regenerated from the same seeded generators that
loaded Postgres (needs `uv run task download`): active fleet only, plus the historical removals that seed case
memory; no answer key. --verify compares a few engines with what Zetaris serves.
Run: uv run task snapshot [--verify]"""

import argparse
import sys

import numpy as np
import pandas as pd

from casefile.load_context import removals_from_history
from data.build_fmea import build as build_fmea
from data.db import load_pack
from data.fleet import SEED, build_fleet
from data.generate_cmms import build_history, build_planning
from settings import get_settings
from tools.data_access import (
    ASSET_COLUMNS, CYCLE_COLUMNS, FMEA_COLUMNS, HISTORY_COLUMNS, PART_COLUMNS, SNAPSHOT_DIR, SnapshotSource,
    ZetarisSource,
)

VERIFY_ASSETS = ["FD001-A001", "FD003-A017", "FD003-A100"]


def build_tables() -> dict[str, pd.DataFrame]:
    cycles_per_day = load_pack()["thresholds"]["cycles_per_day"]
    assets, cycles = build_fleet(cycles_per_day)
    rng = np.random.default_rng(SEED + 1)
    history = build_history(assets, cycles_per_day, rng)
    planning = build_planning(assets, cycles_per_day, rng)

    active = set(assets.loc[assets.fleet_status == "active", "asset_id"])
    registry = assets.assign(esn=[f"ESN-{700001 + i}" for i in range(len(assets))])[ASSET_COLUMNS]
    wo = history["work_orders"]
    joined = wo.merge(history["findings"][["wo_id", "severity", "failure_mode", "note"]], on="wo_id", how="left")
    parts = planning["parts"].merge(planning["stock"], on="part_number", how="left")
    fmea = build_fmea()[["fmea_id", "failure_mode", "task", "task_type", "trigger_severity", "part_number", "qty"]]
    fmea = fmea.merge(parts[["part_number", "qty_on_hand", "qty_reserved", "lead_time_days"]], on="part_number", how="left")
    slots = planning["hangar_slots"]
    removals, first_warnings = removals_from_history(wo, history["findings"])

    return {
        "assets": registry,
        "cycles": cycles.loc[cycles.asset_id.isin(active), CYCLE_COLUMNS],
        "history": joined.loc[joined.asset_id.isin(active), ["asset_id", *HISTORY_COLUMNS]],
        "parts": parts[PART_COLUMNS],
        "hangar_slots": slots.loc[slots.status == "free", ["slot_date", "slot_no"]],
        "flight_schedule": planning["flight_schedule"],
        "fmea_tasks": fmea[FMEA_COLUMNS],
        "removals": removals,
        "first_warnings": first_warnings,
    }


def write(tables: dict[str, pd.DataFrame]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        path = SNAPSHOT_DIR / f"{name}.parquet"
        frame.reset_index(drop=True).to_parquet(path, index=False, compression="zstd")
        print(f"  {name}: {len(frame):,} rows, {path.stat().st_size / 1024:.0f} KB")


def _same(live: pd.DataFrame, snap: pd.DataFrame) -> str | None:
    if list(live.columns) != list(snap.columns):
        return f"columns differ: {list(live.columns)} vs {list(snap.columns)}"
    if len(live) != len(snap):
        return f"{len(live)} live rows vs {len(snap)} snapshot rows"
    for column in live.columns:
        a, b = live[column].reset_index(drop=True), snap[column].reset_index(drop=True)
        if pd.api.types.is_float_dtype(a):
            if not np.allclose(a.to_numpy(float), b.to_numpy(float), equal_nan=True):
                return f"{column} differs"
        elif not a.astype(str).equals(b.astype(str)):
            return f"{column} differs"
    return None


def verify() -> bool:
    live, snap = ZetarisSource(get_settings()), SnapshotSource()
    ok = True
    try:
        checks = [("free_slots", ()), ("parts", (["TF-HPC-1101", "TF-FAN-2101", "TF-GEN-8101"],)),
                  ("fmea_tasks", ("hpc_degradation",))]
        checks += [(m, (a,)) for a in VERIFY_ASSETS for m in ("asset", "cycles", "history", "flight_schedule")]
        for method, args in checks:
            left, right = getattr(live, method)(*args), getattr(snap, method)(*args)
            problem = _same(left.frame, right.frame)
            ok &= problem is None
            print(f"  {method}{args}: {'OK' if problem is None else 'MISMATCH ' + problem} ({left.duration_s}s live)")
    finally:
        live.close()
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="compare the snapshot with Zetaris for a few engines")
    args = parser.parse_args()
    print(f"snapshot -> {SNAPSHOT_DIR}")
    write(build_tables())
    if args.verify:
        print("verify against Zetaris:")
        return 0 if verify() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
