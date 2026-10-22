"""Generate synthetic maintenance history, spares and schedule from the degradation curves.
Run: uv run task seed-cmms [--target local]"""

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from data.db import connect, copy_frame, drop_smoke, grant, load_pack
from data.fleet import REFERENCE_TIME, SEED, build_fleet

PARTS_CSV = Path(__file__).resolve().parent / "seed" / "parts.csv"
INSPECTION_INTERVAL = 60
SLOT_DAYS = 60
SCHEDULE_DAYS = 30
SLOTS_PER_DAY = 2

REMOVAL_PARTS = {
    "hpc_degradation": [("TF-HPC-1101", 1), ("TF-HPC-1104", 1), ("TF-GEN-8102", 1)],
    "fan_degradation": [("TF-FAN-2101", 1), ("TF-FAN-2103", 1), ("TF-GEN-8102", 1)],
}
NOTES = {
    ("hpc", "minor"): "SCHED BORESCOPE INSP HPC STG 1-3 AT {cycle} CYC. FOUND LIGHT LEADING EDGE EROSION ON {n} BLADES. WITHIN AMM LIMITS. REINSPECT {interval} CYC.",
    ("hpc", "moderate"): "BORESCOPE INSP HPC AT {cycle} CYC. FOUND LEADING EDGE EROSION AND TIP CURL ON {n} STG {stage} BLADES, APPROACHING AMM LIMITS. REINSPECT INTERVAL REDUCED TO 30 CYC.",
    ("hpc", "severe"): "BORESCOPE INSP HPC AT {cycle} CYC. FOUND TIP LOSS AND COATING SPALLATION ON STG {stage} BLADES. EXCEEDS AMM LIMITS. ENGINE REMOVAL RECOMMENDED.",
    ("fan", "minor"): "SCHED INSP FAN BLADES AT {cycle} CYC. FOUND MINOR LEADING EDGE NICKS ON {n} BLADES. BLENDED WITHIN LIMITS. REINSPECT {interval} CYC.",
    ("fan", "moderate"): "INSP FAN AT {cycle} CYC. FOUND LEADING EDGE EROSION ON {n} BLADES AND ABRADABLE LINER RUB. BLEND LIMITS NEARLY REACHED. MONITOR VIBRATION.",
    ("fan", "severe"): "INSP FAN AT {cycle} CYC. LEADING EDGE EROSION BEYOND BLEND LIMITS ON {n} BLADES. EXCESSIVE LINER RUB. ENGINE REMOVAL RECOMMENDED.",
    ("engine", "none"): "SCHED BORESCOPE INSP HPC AND FAN AT {cycle} CYC. NO DEFECTS NOTED. REINSPECT {interval} CYC.",
}
REMOVAL_NOTES = {
    "hpc": "ENGINE REMOVED UNSCHEDULED AT {cycle} CYC FOR PERFORMANCE DETERIORATION, EGT MARGIN EXHAUSTED. SHOP FOUND HPC STG 1-3 BLADES ERODED, TIP CLEARANCE BEYOND LIMITS. REPLACED {parts}.",
    "fan": "ENGINE REMOVED UNSCHEDULED AT {cycle} CYC FOR PERFORMANCE DETERIORATION. SHOP FOUND FAN BLADE LEADING EDGES ERODED AND CASE LINER RUB. REPLACED {parts}.",
}
LINE_NOTE = "ROUTINE LINE CHECK AT {cycle} CYC. OIL FILTER REPLACED. NO METAL ON CHIP DETECTOR."


def severity_for(life_fraction: float) -> str:
    if life_fraction < 0.55:
        return "none"
    if life_fraction < 0.75:
        return "minor"
    if life_fraction < 0.9:
        return "moderate"
    return "severe"


def build_history(assets: pd.DataFrame, cycles_per_day: int, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    step = timedelta(hours=24 / cycles_per_day)
    work_orders, findings, parts = [], [], []

    def add(asset, wo_type, cycle, component, severity, failure_mode, note, used=()):
        wo_id = f"WO-{len(work_orders) + 1:06d}"
        opened = asset.last_seen - (asset.last_cycle - cycle) * step
        work_orders.append(
            {
                "wo_id": wo_id, "asset_id": asset.asset_id, "wo_type": wo_type, "status": "closed",
                "opened_at": opened, "closed_at": opened + timedelta(hours=int(rng.integers(4, 72))),
                "cycle_at": cycle, "component": component, "description": note.split(". ")[0] + ".",
            }
        )
        if severity is not None:
            findings.append(
                {
                    "finding_id": f"FN-{len(findings) + 1:06d}", "wo_id": wo_id, "asset_id": asset.asset_id,
                    "cycle_at": cycle, "component": component, "failure_mode": failure_mode,
                    "severity": severity, "note": note,
                }
            )
        parts.extend({"wo_id": wo_id, "part_number": p, "qty": q} for p, q in used)

    for asset in assets.itertuples():
        component = "hpc" if asset.proxy_fault_mode == "hpc_degradation" else "fan"
        total_life = asset.last_cycle + asset.true_rul_at_last_cycle
        for cycle in range(INSPECTION_INTERVAL, asset.last_cycle + 1, INSPECTION_INTERVAL):
            if asset.fleet_status == "removed" and cycle >= asset.last_cycle:
                break
            severity = severity_for(cycle / total_life + rng.normal(0, 0.05))
            where, mode = ("engine", None) if severity == "none" else (component, asset.proxy_fault_mode)
            note = NOTES[(where, severity)].format(
                cycle=cycle, n=int(rng.integers(2, 9)), stage=int(rng.integers(1, 4)), interval=INSPECTION_INTERVAL
            )
            add(asset, "inspection", cycle, where, severity, mode, note)
            if rng.random() < 0.3:
                line_cycle = cycle - int(rng.integers(5, 30))
                add(asset, "line_maintenance", line_cycle, "engine", None, None, LINE_NOTE.format(cycle=line_cycle),
                    [("TF-GEN-8103", 1)])
        if asset.fleet_status == "removed":
            used = REMOVAL_PARTS[asset.proxy_fault_mode]
            note = REMOVAL_NOTES[component].format(cycle=asset.last_cycle, parts=", ".join(p for p, _ in used))
            add(asset, "unscheduled_removal", asset.last_cycle, component, "severe", asset.proxy_fault_mode, note, used)

    work_orders = pd.DataFrame(work_orders).sort_values(["asset_id", "opened_at"])
    return {"work_orders": work_orders, "findings": pd.DataFrame(findings), "parts_replaced": pd.DataFrame(parts)}


def build_planning(assets: pd.DataFrame, cycles_per_day: int, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    catalogue = pd.read_csv(PARTS_CSV)
    parts = catalogue[["part_number", "description", "component", "failure_mode", "lead_time_days", "unit_cost_usd"]]
    stock = catalogue[["part_number", "qty_on_hand"]].assign(location="MAIN-STORES", qty_reserved=0)

    active = assets.loc[assets.fleet_status == "active", "asset_id"].to_numpy()
    start = REFERENCE_TIME.date() + timedelta(days=1)
    slots, booked = [], set()
    for day in range(SLOT_DAYS):
        slot_date = start + timedelta(days=day)
        for slot_no in range(1, SLOTS_PER_DAY + 1):
            if rng.random() < 0.35:
                asset_id = str(rng.choice(active))
                booked.add((asset_id, slot_date))
                slots.append({"slot_date": slot_date, "slot_no": slot_no, "status": "booked", "asset_id": asset_id,
                              "purpose": "scheduled check"})
            else:
                slots.append({"slot_date": slot_date, "slot_no": slot_no, "status": "free", "asset_id": None,
                              "purpose": None})

    schedule = [
        {"asset_id": a, "flight_date": start + timedelta(days=d),
         "planned_cycles": 0 if (a, start + timedelta(days=d)) in booked else cycles_per_day}
        for a in active for d in range(SCHEDULE_DAYS)
    ]
    return {"parts": parts, "stock": stock[["part_number", "location", "qty_on_hand", "qty_reserved"]],
            "hangar_slots": pd.DataFrame(slots), "flight_schedule": pd.DataFrame(schedule)}


DDL = """
DROP TABLE IF EXISTS parts_replaced, findings, work_orders, assets;
DROP TABLE IF EXISTS planning_flight_schedule, planning_hangar_slots, planning_stock, planning_parts;

CREATE TABLE assets (
    asset_id text PRIMARY KEY,
    asset_type text NOT NULL,
    dataset text NOT NULL,
    esn text NOT NULL UNIQUE,
    fleet_status text NOT NULL CHECK (fleet_status IN ('active', 'removed')),
    last_cycle integer NOT NULL,
    last_seen timestamptz NOT NULL
);
CREATE TABLE work_orders (
    wo_id text PRIMARY KEY,
    asset_id text NOT NULL REFERENCES assets,
    wo_type text NOT NULL,
    status text NOT NULL,
    opened_at timestamptz NOT NULL,
    closed_at timestamptz,
    cycle_at integer NOT NULL,
    component text NOT NULL,
    description text NOT NULL,
    origin text NOT NULL DEFAULT 'synthetic'
);
CREATE TABLE findings (
    finding_id text PRIMARY KEY,
    wo_id text NOT NULL REFERENCES work_orders,
    asset_id text NOT NULL REFERENCES assets,
    cycle_at integer NOT NULL,
    component text NOT NULL,
    failure_mode text,
    severity text NOT NULL CHECK (severity IN ('none', 'minor', 'moderate', 'severe')),
    note text NOT NULL
);
CREATE TABLE parts_replaced (
    wo_id text NOT NULL REFERENCES work_orders,
    part_number text NOT NULL,
    qty integer NOT NULL
);
CREATE INDEX ON work_orders (asset_id, opened_at);
CREATE INDEX ON findings (asset_id, cycle_at);

CREATE TABLE planning_parts (
    part_number text PRIMARY KEY,
    description text NOT NULL,
    component text NOT NULL,
    failure_mode text,
    lead_time_days integer NOT NULL,
    unit_cost_usd numeric NOT NULL
);
CREATE TABLE planning_stock (
    part_number text PRIMARY KEY REFERENCES planning_parts,
    location text NOT NULL,
    qty_on_hand integer NOT NULL,
    qty_reserved integer NOT NULL
);
CREATE TABLE planning_hangar_slots (
    slot_date date NOT NULL,
    slot_no integer NOT NULL,
    status text NOT NULL CHECK (status IN ('free', 'booked')),
    asset_id text,
    purpose text,
    PRIMARY KEY (slot_date, slot_no)
);
CREATE TABLE planning_flight_schedule (
    asset_id text NOT NULL,
    flight_date date NOT NULL,
    planned_cycles integer NOT NULL,
    PRIMARY KEY (asset_id, flight_date)
);
COMMENT ON TABLE work_orders IS 'Synthetic: generated from C-MAPSS degradation curves, wording styled on FAA SDR.';
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["rds", "local"], default="rds")
    args = parser.parse_args()

    cycles_per_day = load_pack()["thresholds"]["cycles_per_day"]
    assets, _ = build_fleet(cycles_per_day)
    rng = np.random.default_rng(SEED + 1)
    history = build_history(assets, cycles_per_day, rng)
    planning = build_planning(assets, cycles_per_day, rng)
    registry = assets.assign(esn=[f"ESN-{700001 + i}" for i in range(len(assets))])[
        ["asset_id", "asset_type", "dataset", "esn", "fleet_status", "last_cycle", "last_seen"]
    ]

    with connect("cmms", args.target) as conn:
        conn.execute(DDL)
        copy_frame(conn, "assets", registry)
        for table in ("work_orders", "findings", "parts_replaced"):
            copy_frame(conn, table, history[table])
        for table in ("parts", "stock", "hangar_slots", "flight_schedule"):
            copy_frame(conn, f"planning_{table}", planning[table])

        grant(conn, "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}", "zetaris_ro")
        grant(conn, "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role}", "app_rw")
        drop_smoke(conn)
        conn.commit()

    sizes = {**{k: len(v) for k, v in history.items()}, **{k: len(v) for k, v in planning.items()}}
    print(f"cmms [{args.target}]: {len(registry)} assets, " + ", ".join(f"{v:,} {k}" for k, v in sizes.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
