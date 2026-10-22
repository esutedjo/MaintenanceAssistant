from datetime import datetime, timezone

import httpx
import pandas as pd
import pytest

from casefile.schemas import EvidenceLedger
from data.db import load_pack
from settings import Settings
from tools.data_access import FallbackSource, SnapshotSource, coerce, open_data_source, safe_id
from tools.data_tools import AssetNotFound, DataTools
from tools.zetaris import ZetarisError

REFERENCE = datetime(2026, 10, 22, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def snapshot():
    return SnapshotSource()


@pytest.fixture
def tools(snapshot):
    return DataTools(snapshot, EvidenceLedger("data"), load_pack(), REFERENCE)


class BrokenLive(SnapshotSource):
    """Behaves like Zetaris going down: every call raises."""

    def __init__(self, error: Exception):
        self.error, self.calls = error, 0

    def _table(self, name):
        self.calls += 1
        raise self.error


def test_safe_id_rejects_sql():
    assert safe_id("FD003-A017") == "FD003-A017"
    for bad in ["x' OR '1'='1", "", "a;b", "a" * 41]:
        with pytest.raises(ValueError):
            safe_id(bad)


def test_coerce_turns_zetaris_strings_into_typed_columns():
    frame = coerce(pd.DataFrame({"cycle": ["3", None], "s_2": ["642.5", "643"], "ts": ["2026-10-21T12:00:00Z", None],
                                 "slot_date": ["2026-10-23", "2026-10-24"], "wo_type": ["inspection", None]}))
    assert str(frame.cycle.dtype) == "Int64" and frame.cycle.isna().iloc[1]
    assert frame.s_2.dtype == float
    assert str(frame.ts.dt.tz) == "UTC"
    assert frame.slot_date.iloc[0].isoformat() == "2026-10-23"
    assert frame.wo_type.iloc[1] is None


def test_snapshot_has_no_answer_key(snapshot):
    for name in ["assets", "cycles", "history"]:
        columns = set(snapshot._table(name).columns)
        assert not columns & {"true_rul", "rul", "proxy_fault_mode"}, name
    assert set(snapshot._table("cycles").asset_id.str[6]) == {"A"}


def test_snapshot_queries_filter_by_asset(snapshot):
    cycles = snapshot.cycles("FD003-A017").frame
    assert set(cycles.asset_id) == {"FD003-A017"} and cycles.cycle.is_monotonic_increasing
    assert snapshot.asset("ESN-700001").frame.asset_id.tolist() == snapshot.asset("FD001-H001").frame.asset_id.tolist()
    assert snapshot.cycles("FD003-Z999").frame.empty


@pytest.mark.parametrize("error", [ZetarisError("boom"), httpx.ConnectTimeout("slow")])
def test_fallback_switches_to_snapshot_once_and_flags_it(snapshot, error):
    live = BrokenLive(error)
    source = FallbackSource(live, snapshot)
    first = source.cycles("FD003-A017")
    second = source.history("FD003-A017")
    assert live.calls == 1
    assert source.mode == "snapshot"
    assert first.degraded and "snapshot" in first.degraded and second.degraded == first.degraded
    assert not first.frame.empty


def test_sample_mode_never_touches_zetaris():
    assert isinstance(open_data_source(Settings(sample_mode=True, zetaris_mode="live")), SnapshotSource)


def test_telemetry_evidence_summarises_degrading_sensors(tools):
    telemetry = tools.telemetry("FD003-A017")
    ev = telemetry.evidence
    assert ev.id == "EV-DAT-01" and ev.kind == "query" and ev.source == "snapshot:cycles.parquet"
    assert ev.content["rows"] == len(telemetry.cycles)
    assert "s_1" not in ev.content["sensors"] and "s_12" in ev.content["sensors"]
    assert ev.content["sensors"]["s_12"]["component"] == "combustor"
    assert "cycles" in ev.summary


def test_history_parts_slots_schedule_and_fmea(tools):
    history = tools.history("FD003-A017")
    assert history.content["by_type"] == {"inspection": 3, "line_maintenance": 1}
    assert history.content["last_inspection"]["cycle"] == 180

    parts = tools.parts(["TF-HPC-1101", "TF-NOPE-0000"])
    assert parts.content["parts"][0]["lead_time_days"] == 35 and parts.content["unknown"] == ["TF-NOPE-0000"]

    slots = tools.free_slots()
    assert slots.content["days_to_first_free"] >= 0 and slots.content["first_free"] >= "2026-10-22"

    schedule = tools.flight_schedule("FD003-A017")
    assert schedule.content["cycles_next_30_days"] >= schedule.content["cycles_next_7_days"] > 0

    fmea = tools.fmea_tasks("hpc_degradation")
    replace = next(t for t in fmea.content["tasks"] if t["fmea_id"] == "FM-HPC-04")
    assert {p["part_number"] for p in replace["parts"]} >= {"TF-HPC-1101", "TF-HPC-1104"}
    assert [e.id for e in [history, parts, slots, schedule, fmea]] == [f"EV-DAT-0{i}" for i in range(1, 6)]


def test_unknown_asset_raises(tools):
    with pytest.raises(AssetNotFound):
        tools.asset_profile("FD003-Z999")
