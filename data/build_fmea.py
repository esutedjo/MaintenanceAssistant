"""Validate the hand-written FMEA, add sensor signatures from the asset pack and write Parquet for S3.
Run: uv run task fmea  (then upload data/export/fmea.parquet to s3://<bucket>/fmea/)"""

import sys
from pathlib import Path

import pandas as pd

from data.db import PACK_DIR, load_pack
from settings import ROOT

PARTS_CSV = ROOT / "data" / "seed" / "parts.csv"
OUT = ROOT / "data" / "export" / "fmea.parquet"
SEVERITIES = {"minor", "moderate", "severe"}
TASK_TYPES = {"inspect", "restore", "replace", "engine_change"}
SOURCE = "synthetic; fault physics from Saxena et al. 2008 (C-MAPSS)"


def signature(pack: dict, failure_mode: str, discriminating_only: bool = False) -> str:
    """Sensors that move under this failure mode, e.g. 's_12 phi down; s_7 P30 down'."""
    modes = list(pack["failure_modes"])
    parts = []
    for sensor, spec in pack["sensors"].items():
        direction = spec["direction"][failure_mode]
        if direction == "none":
            continue
        if discriminating_only and len({spec["direction"][m] for m in modes}) == 1:
            continue
        parts.append(f"{sensor} {spec['name']} {direction}")
    return "; ".join(parts)


def validate(fmea: pd.DataFrame, pack: dict, parts: pd.DataFrame) -> list[str]:
    errors = []
    catalogue = parts.set_index("part_number")
    for i, row in fmea.iterrows():
        where = f"row {i + 2} ({row.fmea_id})"
        mode = pack["failure_modes"].get(row.failure_mode)
        if mode is None:
            errors.append(f"{where}: unknown failure_mode {row.failure_mode}")
        elif mode["component"] != row.component:
            errors.append(f"{where}: component {row.component} does not match pack ({mode['component']})")
        if row.trigger_severity not in SEVERITIES:
            errors.append(f"{where}: trigger_severity {row.trigger_severity} not in {sorted(SEVERITIES)}")
        if row.task_type not in TASK_TYPES:
            errors.append(f"{where}: task_type {row.task_type} not in {sorted(TASK_TYPES)}")
        if pd.isna(row.part_number):
            if pd.notna(row.qty):
                errors.append(f"{where}: qty given without part_number")
            continue
        if row.part_number not in catalogue.index:
            errors.append(f"{where}: part {row.part_number} not in parts catalogue")
            continue
        part_mode = catalogue.at[row.part_number, "failure_mode"]
        if pd.notna(part_mode) and part_mode != row.failure_mode:
            errors.append(f"{where}: part {row.part_number} belongs to {part_mode}")
        if pd.isna(row.qty) or row.qty < 1:
            errors.append(f"{where}: qty must be >= 1")
    duplicates = fmea.part_number.notna() & fmea.duplicated(["fmea_id", "part_number"])
    errors += [f"row {i + 2}: duplicate fmea_id and part_number" for i in fmea.index[duplicates]]
    return errors


def build(pack_dir: Path = PACK_DIR) -> pd.DataFrame:
    pack = load_pack(pack_dir)
    fmea = pd.read_csv(pack_dir / pack["failure_modes_csv"], dtype={"qty": "Int64"})
    errors = validate(fmea, pack, pd.read_csv(PARTS_CSV))
    if errors:
        raise ValueError("fmea.csv is invalid:\n" + "\n".join(errors))
    return fmea.assign(
        failure_mode_label=fmea.failure_mode.map(lambda m: pack["failure_modes"][m]["label"]),
        sensor_signature=fmea.failure_mode.map(lambda m: signature(pack, m)),
        discriminating_sensors=fmea.failure_mode.map(lambda m: signature(pack, m, discriminating_only=True)),
        asset_type=pack["asset_type"],
        pack_version=pack["version"],
        source=SOURCE,
    )


def main() -> int:
    fmea = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fmea.to_parquet(OUT, index=False)
    print(f"fmea: {len(fmea)} rows, {fmea.fmea_id.nunique()} tasks -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
