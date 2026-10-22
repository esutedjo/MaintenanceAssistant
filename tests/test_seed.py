from datetime import timedelta

import numpy as np
import pandas as pd

from data.fleet import REFERENCE_TIME, asset_id, proxy_fault_mode
from data.generate_cmms import INSPECTION_INTERVAL, build_history, build_planning, severity_for


def _trend(unit: int, slope: float, n: int = 100) -> pd.DataFrame:
    return pd.DataFrame({"unit": unit, "cycle": range(1, n + 1), "s_12": 520 + slope * np.arange(n)})


def _assets() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"asset_id": "FD003-H001", "fleet_status": "removed", "last_cycle": 200, "true_rul_at_last_cycle": 0,
             "proxy_fault_mode": "fan_degradation", "last_seen": REFERENCE_TIME - timedelta(days=100)},
            {"asset_id": "FD001-A001", "fleet_status": "active", "last_cycle": 130, "true_rul_at_last_cycle": 20,
             "proxy_fault_mode": "hpc_degradation", "last_seen": REFERENCE_TIME},
        ]
    )


def test_asset_id_format():
    assert asset_id("FD001", "test", 7) == "FD001-A007"
    assert asset_id("FD003", "train", 42) == "FD003-H042"


def test_proxy_fault_mode_uses_phi_trend_in_fd003():
    frame = pd.concat([_trend(1, 0.05), _trend(2, -0.05)])
    labels = proxy_fault_mode(frame, "FD003")
    assert labels[1] == "fan_degradation"
    assert labels[2] == "hpc_degradation"


def test_proxy_fault_mode_fd001_is_always_hpc():
    assert set(proxy_fault_mode(_trend(1, 0.05), "FD001")) == {"hpc_degradation"}


def test_severity_thresholds():
    assert [severity_for(f) for f in (0.2, 0.6, 0.8, 0.95)] == ["none", "minor", "moderate", "severe"]


def test_history_removes_only_retired_engines_at_their_last_cycle():
    history = build_history(_assets(), cycles_per_day=2, rng=np.random.default_rng(0))
    removals = history["work_orders"].query("wo_type == 'unscheduled_removal'")
    assert removals[["asset_id", "cycle_at"]].values.tolist() == [["FD003-H001", 200]]
    assert set(history["parts_replaced"].query("wo_id in @removals.wo_id").part_number) >= {"TF-FAN-2101"}


def test_history_inspects_on_interval_and_never_after_last_cycle():
    wos = build_history(_assets(), cycles_per_day=2, rng=np.random.default_rng(0))["work_orders"]
    inspections = wos.query("wo_type == 'inspection'")
    assert (inspections.cycle_at % INSPECTION_INTERVAL == 0).all()
    active = inspections.query("asset_id == 'FD001-A001'")
    assert active.cycle_at.max() <= 130
    assert (active.opened_at <= REFERENCE_TIME).all()


def test_planning_slots_and_schedule():
    planning = build_planning(_assets(), cycles_per_day=2, rng=np.random.default_rng(0))
    slots = planning["hangar_slots"]
    assert slots.groupby("slot_date").size().eq(2).all()
    assert set(planning["flight_schedule"].asset_id) == {"FD001-A001"}
    schedule = planning["flight_schedule"]
    booked_days = set(slots.query("asset_id == 'FD001-A001'").slot_date) & set(schedule.flight_date)
    assert set(schedule.query("planned_cycles == 0").flight_date) == booked_days
