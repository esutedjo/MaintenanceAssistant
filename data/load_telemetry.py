"""Load C-MAPSS FD001/FD003 into the telemetry database. Run: uv run task seed-telemetry [--target local]"""

import argparse
import sys

from data.db import connect, copy_frame, drop_smoke, grant, load_pack
from data.fleet import SENSORS, build_fleet

SENSOR_COLUMNS = ",\n    ".join(f"{s} double precision" for s in SENSORS)
LONG_VALUES = ", ".join(f"('{s}', c.{s})" for s in SENSORS)

DDL = f"""
DROP VIEW IF EXISTS telemetry_long;
DROP TABLE IF EXISTS cycles, signals, eval_ground_truth;

CREATE TABLE signals (
    signal text PRIMARY KEY,
    name text NOT NULL,
    unit text NOT NULL,
    component text NOT NULL,
    description text NOT NULL
);

CREATE TABLE cycles (
    asset_id text NOT NULL,
    asset_type text NOT NULL,
    dataset text NOT NULL,
    cycle integer NOT NULL,
    ts timestamptz NOT NULL,
    operating_regime text NOT NULL,
    setting_1 double precision,
    setting_2 double precision,
    setting_3 double precision,
    {SENSOR_COLUMNS},
    PRIMARY KEY (asset_id, cycle)
);

CREATE VIEW telemetry_long AS
SELECT c.asset_id, c.asset_type, c.cycle, c.ts, v.signal, v.value, s.unit, c.operating_regime
FROM cycles c
CROSS JOIN LATERAL (VALUES {LONG_VALUES}) AS v(signal, value)
JOIN signals s ON s.signal = v.signal;

COMMENT ON TABLE cycles IS 'NASA C-MAPSS FD001/FD003 (simulated). Timestamps are synthetic.';

CREATE TABLE eval_ground_truth (
    asset_id text PRIMARY KEY,
    dataset text NOT NULL,
    source_split text NOT NULL,
    source_unit integer NOT NULL,
    last_cycle integer NOT NULL,
    true_rul_at_last_cycle integer NOT NULL,
    proxy_fault_mode text NOT NULL
);
COMMENT ON TABLE eval_ground_truth IS 'Evaluation only. Never readable by zetaris_ro or app_rw.';
REVOKE ALL ON eval_ground_truth FROM PUBLIC;
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["rds", "local"], default="rds")
    args = parser.parse_args()

    pack = load_pack()
    assets, cycles = build_fleet(pack["thresholds"]["cycles_per_day"])
    signals = [
        (code, s["name"], s["unit"], s["component"], s["description"]) for code, s in pack["sensors"].items()
    ]
    truth = assets[
        ["asset_id", "dataset", "source_split", "source_unit", "last_cycle", "true_rul_at_last_cycle", "proxy_fault_mode"]
    ]

    with connect("telemetry", args.target) as conn:
        conn.execute(DDL)
        conn.cursor().executemany("INSERT INTO signals VALUES (%s, %s, %s, %s, %s)", signals)
        copy_frame(conn, "cycles", cycles)
        copy_frame(conn, "eval_ground_truth", truth)
        grant(conn, "GRANT SELECT ON cycles, signals, telemetry_long TO {role}", "zetaris_ro")
        for role in ("zetaris_ro", "app_rw"):
            grant(conn, "REVOKE ALL ON eval_ground_truth FROM {role}", role)
        drop_smoke(conn)
        conn.commit()
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM cycles), (SELECT count(DISTINCT asset_id) FROM cycles), "
            "(SELECT count(*) FROM eval_ground_truth WHERE source_split = 'test')"
        ).fetchone()

    print(f"telemetry [{args.target}]: {counts[0]:,} cycle rows, {counts[1]} engines, {counts[2]} active engines with ground truth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
