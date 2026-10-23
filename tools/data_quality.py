"""Input plausibility checks. Deterministic, no LLM. Values that cannot come from a working engine or a
consistent record (outside the physical range seen on any engine, impossible jumps, a frozen sensor, broken
cycle order, work orders after the last cycle) are reported as issues. Limits come from the asset pack."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from data.db import PACK_DIR

Severity = Literal["minor", "serious"]


@dataclass
class Issue:
    check: str
    severity: Severity
    detail: str
    sensor: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@lru_cache(maxsize=4)
def _ranges(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["sensors"]


def sensor_ranges(pack: dict, pack_dir: Path = PACK_DIR) -> dict:
    return _ranges(pack_dir / pack["data_quality"]["ranges_file"])


def model_sensors(pack: dict) -> set[str]:
    """Sensors the RUL and fault-mode models read: the ones that move under at least one failure mode."""
    return {s for s, spec in pack["sensors"].items() if set(spec["direction"].values()) != {"none"}}


def _name(pack: dict, sensor: str) -> str:
    return f"{pack['sensors'][sensor]['name']} ({sensor})"


def check_cycles(frame: pd.DataFrame, registry_last_cycle: int | None, reference_time: datetime) -> list[Issue]:
    issues = []
    cycles = pd.to_numeric(frame["cycle"], errors="coerce")
    if cycles.isna().any():
        issues.append(Issue("cycle_missing", "serious", f"{int(cycles.isna().sum())} rows without a cycle number"))
        cycles = cycles.dropna()
    if cycles.duplicated().any():
        issues.append(Issue("cycle_duplicate", "serious", f"cycles recorded twice: {sorted(cycles[cycles.duplicated()].astype(int))[:5]}"))
    if not cycles.is_monotonic_increasing:
        issues.append(Issue("cycle_order", "serious", "cycle numbers go backwards in the time series"))
    elif len(cycles) > 1:
        gaps = cycles.diff().dropna()
        if (gaps > 1).any():
            issues.append(Issue("cycle_gap", "minor", f"{int((gaps > 1).sum())} gap(s) in the cycle sequence, "
                                                     f"{int(gaps.max() - 1)} cycles missing at the largest"))
    if len(cycles) and registry_last_cycle is not None and int(cycles.max()) != int(registry_last_cycle):
        issues.append(Issue("registry_mismatch", "serious", f"telemetry ends at cycle {int(cycles.max())} but the "
                                                           f"asset registry says {int(registry_last_cycle)}"))
    if "ts" in frame:
        ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
        future = ts > pd.Timestamp(reference_time) + pd.Timedelta(hours=1)
        if future.any():
            issues.append(Issue("timestamp_future", "minor", f"{int(future.sum())} readings dated after the reference "
                                                            f"time {reference_time:%Y-%m-%d %H:%M}"))
    return issues


def check_sensors(frame: pd.DataFrame, pack: dict) -> list[Issue]:
    cfg, ranges, used = pack["data_quality"], sensor_ranges(pack), model_sensors(pack)
    issues = []
    for sensor in pack["sensors"]:
        if sensor not in frame:
            issues.append(Issue("sensor_missing", "serious" if sensor in used else "minor",
                                f"{_name(pack, sensor)} is not in the telemetry", sensor))
            continue
        severity: Severity = "serious" if sensor in used else "minor"
        values = pd.to_numeric(frame[sensor], errors="coerce").to_numpy(float)
        missing = np.isnan(values)
        if missing.mean() > cfg["max_missing_share"]:
            issues.append(Issue("missing_values", severity, f"{_name(pack, sensor)}: {int(missing.sum())} of "
                                                            f"{len(values)} readings missing", sensor))
        present = values[~missing]
        if not len(present):
            continue
        r = ranges[sensor]
        span = r["max"] - r["min"]
        tol = max(cfg["range_margin"] * span, cfg["min_margin"] * max(abs(r["min"]), abs(r["max"])))
        low, high = r["min"] - tol, r["max"] + tol
        outside = (present < low) | (present > high)
        if outside.any():
            worst = present[outside][np.argmax(np.abs(present[outside] - (low + high) / 2))]
            issues.append(Issue("out_of_range", severity, f"{_name(pack, sensor)} = {worst:g} {pack['sensors'][sensor]['unit']}"
                                                          f" is outside the plausible {low:g}..{high:g}", sensor))
        if r["max_step"] > 0 and len(present) > 1:
            step = np.abs(np.diff(present)).max()
            if step > cfg["step_factor"] * r["max_step"]:
                issues.append(Issue("impossible_step", severity, f"{_name(pack, sensor)} jumps {step:g} between "
                                                                 f"cycles (largest seen on any engine {r['max_step']:g})", sensor))
        if sensor in used and span > 0:
            run = _longest_constant_run(present)
            if run >= cfg["frozen_cycles"]:
                issues.append(Issue("frozen_sensor", "serious", f"{_name(pack, sensor)} repeats the same value for "
                                                                f"{run} cycles", sensor))
    return issues


def _longest_constant_run(values: np.ndarray) -> int:
    if len(values) == 0:
        return 0
    changes = np.flatnonzero(np.diff(values) != 0)
    bounds = np.concatenate(([-1], changes, [len(values) - 1]))
    return int(np.diff(bounds).max())


def check_history(frame: pd.DataFrame, last_cycle: int | None, fleet_status: str | None,
                  reference_time: datetime) -> list[Issue]:
    issues = []
    if frame.empty:
        return issues
    cycles = pd.to_numeric(frame["cycle_at"], errors="coerce")
    if last_cycle is not None and (cycles > last_cycle).any():
        issues.append(Issue("history_after_last_cycle", "minor", f"{int((cycles > last_cycle).sum())} work order(s) "
                                                                 f"after the last recorded cycle {last_cycle}"))
    opened = pd.to_datetime(frame["opened_at"], utc=True, errors="coerce")
    if (opened > pd.Timestamp(reference_time) + pd.Timedelta(days=1)).any():
        issues.append(Issue("history_future", "minor", "work orders dated after the reference time"))
    if fleet_status == "active" and frame["wo_type"].astype(str).str.contains("removal").any():
        issues.append(Issue("removed_but_active", "serious", "a removal is recorded but the asset registry lists the "
                                                             "engine as active"))
    return issues


def check_parts(rows: list[dict]) -> list[Issue]:
    issues = []
    for row in rows:
        on_hand, reserved, lead = row.get("qty_on_hand"), row.get("qty_reserved"), row.get("lead_time_days")
        number = row.get("part_number")
        if on_hand is not None and on_hand < 0:
            issues.append(Issue("negative_stock", "serious", f"{number}: {on_hand} on hand"))
        elif reserved is not None and on_hand is not None and reserved > on_hand:
            issues.append(Issue("reserved_above_stock", "serious", f"{number}: {reserved} reserved but {on_hand} on hand"))
        if lead is not None and lead < 0:
            issues.append(Issue("negative_lead_time", "serious", f"{number}: lead time {lead} d"))
    return issues


def overall(issues: list[Issue]) -> str:
    if any(i.severity == "serious" for i in issues):
        return "serious"
    return "minor" if issues else "ok"
