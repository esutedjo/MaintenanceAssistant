import numpy as np
import pandas as pd
import pytest

from data.db import load_pack
from data.fleet import REFERENCE_TIME
from evaluation.data_faults import DATA_FAULTS, CorruptSource
from graph.runner import execute_run
from settings import Settings
from tools.data_access import SnapshotSource
from tools.data_quality import check_cycles, check_history, check_parts, check_sensors, overall
from tools.model_tools import ModelTools

PACK = load_pack()
SOURCE = SnapshotSource()


@pytest.fixture
def settings(tmp_path):
    return Settings(sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def _cycles(asset_id: str = "FD001-A031") -> pd.DataFrame:
    return SOURCE.cycles(asset_id).frame.reset_index(drop=True)


def _checks(name: str, issues) -> set[str]:
    return {i.check for i in issues}


@pytest.mark.parametrize("asset_id", ["FD001-A001", "FD001-A031", "FD003-A100", "FD001-A070"])
def test_clean_snapshot_engines_raise_no_issues(asset_id):
    cycles = _cycles(asset_id)
    last = int(SOURCE.asset(asset_id).frame.last_cycle.iloc[0])
    issues = [*check_cycles(cycles, last, REFERENCE_TIME), *check_sensors(cycles, PACK),
              *check_history(SOURCE.history(asset_id).frame, last, "active", REFERENCE_TIME)]
    assert issues == []


def test_cycle_checks():
    cycles = _cycles()
    last = int(cycles.cycle.max())
    assert _checks("", check_cycles(pd.concat([cycles, cycles.tail(2)]), last, REFERENCE_TIME)) >= {
        "cycle_duplicate", "cycle_order"}
    gap = check_cycles(cycles.drop(cycles.index[50:53]), last, REFERENCE_TIME)
    assert [(i.check, i.severity) for i in gap] == [("cycle_gap", "minor")]
    assert _checks("", check_cycles(cycles.iloc[:-5], last, REFERENCE_TIME)) == {"registry_mismatch"}
    future = cycles.assign(ts=pd.Timestamp(REFERENCE_TIME) + pd.Timedelta(days=3))
    assert [(i.check, i.severity) for i in check_cycles(future, last, REFERENCE_TIME)] == [("timestamp_future", "minor")]


def test_sensor_checks_grade_by_whether_the_models_read_the_sensor():
    cycles = _cycles()
    spike = cycles.copy()
    spike.loc[100, "s_3"] += 300
    assert {(i.check, i.severity) for i in check_sensors(spike, PACK)} == {
        ("out_of_range", "serious"), ("impossible_step", "serious")}
    unused = cycles.assign(s_1=600.0)
    assert {(i.check, i.severity) for i in check_sensors(unused, PACK)} == {("out_of_range", "minor")}
    frozen = cycles.copy()
    frozen.loc[frozen.index[-30:], "s_11"] = frozen["s_11"].iloc[-31]
    assert {(i.check, i.sensor) for i in check_sensors(frozen, PACK)} == {("frozen_sensor", "s_11")}
    missing = cycles.copy()
    missing.loc[5, "s_12"] = np.nan
    assert {(i.check, i.severity) for i in check_sensors(missing, PACK)} == {("missing_values", "serious")}
    assert {(i.check, i.severity) for i in check_sensors(cycles.drop(columns="s_4"), PACK)} == {
        ("sensor_missing", "serious")}


def test_history_checks():
    history = pd.DataFrame({"cycle_at": [60, 250], "wo_type": ["inspection", "unscheduled_removal"],
                            "opened_at": [pd.Timestamp(REFERENCE_TIME)] * 2})
    issues = check_history(history, 196, "active", REFERENCE_TIME)
    assert {(i.check, i.severity) for i in issues} == {("history_after_last_cycle", "minor"),
                                                       ("removed_but_active", "serious")}
    assert check_history(history, 300, "removed", REFERENCE_TIME) == []


def test_parts_checks_and_overall():
    issues = check_parts([{"part_number": "A", "qty_on_hand": -1, "qty_reserved": 0, "lead_time_days": 5},
                          {"part_number": "B", "qty_on_hand": 1, "qty_reserved": 2, "lead_time_days": -3},
                          {"part_number": "C", "qty_on_hand": 4, "qty_reserved": 1, "lead_time_days": 10}])
    assert [i.check for i in issues] == ["negative_stock", "reserved_above_stock", "negative_lead_time"]
    assert overall(issues) == "serious" and overall([]) == "ok"


def test_serious_data_issue_escalates_and_lowers_confidence(settings):
    case = execute_run("What should I do about FD001-A031?", settings,
                       data=CorruptSource("FD001-A031", DATA_FAULTS["spike_t30"])).case
    assert case.status == "needs_review"
    quality = case.evidence["EV-DAT-02"]
    assert quality.content["data_quality"] == "serious" and quality.degraded
    escalation = next(e for e in case.escalations if e.rule == "data_quality")
    assert "T30" in escalation.message
    rec = case.recommendation
    assert rec.confidence == "low" and rec.action.startswith("Confirm the input data first")
    assert "EV-DAT-02" in rec.rationale[0].evidence_ids


def test_impossible_stock_counts_as_none_and_escalates(settings):
    case = execute_run("What should I do about FD001-A031?", settings,
                       data=CorruptSource("FD001-A031", DATA_FAULTS["negative_stock"])).case
    assert case.status == "needs_review"
    assert all(p.available >= 0 for p in case.recommendation.parts)
    assert any(e.rule == "data_quality" for e in case.escalations)


def test_minor_data_issue_is_flagged_without_escalation(settings):
    case = execute_run("What should I do about FD001-A031?", settings,
                       data=CorruptSource("FD001-A031", DATA_FAULTS["cycle_gap"])).case
    assert case.status == "needs_review"
    assert any(d.startswith("Data quality minor") for d in case.degraded)
    assert not any(e.rule == "data_quality" for e in case.escalations)
    assert not case.recommendation.action.startswith("Confirm the input data")
    assert case.recommendation.confidence != "low"


def test_clean_run_has_no_data_quality_flag(settings):
    case = execute_run("What should I do about FD001-A031?", settings).case
    assert case.evidence["EV-DAT-02"].content["data_quality"] == "ok"
    assert not any(d.startswith("Data quality") for d in case.degraded)


def test_two_specialists_failing_in_one_step_end_in_error(settings, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("model file corrupt")

    monkeypatch.setattr(ModelTools, "drivers", broken)
    monkeypatch.setattr(ModelTools, "fault_modes", broken)
    assert execute_run("How long can FD003-A100 keep flying?", settings).case.status == "error"
