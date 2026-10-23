import pytest

from casefile.schemas import EvidenceLedger
from data.db import load_pack
from models.fault_mode import discriminating
from tools.data_access import SnapshotSource
from tools.model_tools import ModelTools

PACK = load_pack()


@pytest.fixture(scope="module")
def snapshot():
    return SnapshotSource()


def _run(snapshot, asset_id):
    cycles = snapshot.cycles(asset_id).frame
    variant = snapshot.asset(asset_id).frame.dataset.iloc[0]
    ledger = EvidenceLedger("prognostics")
    tools = ModelTools(PACK, ledger)
    a, b = tools.rul_ml(asset_id, cycles), tools.rul_trend(asset_id, cycles)
    return (ledger, a, b, tools.drivers(asset_id, cycles), tools.fault_modes(asset_id, cycles, variant),
            tools.anomaly(asset_id, cycles))


def test_discriminating_sensors_come_from_the_pack():
    both = discriminating(PACK, ["hpc_degradation", "fan_degradation"])
    assert set(both["hpc_degradation"]) == {"s_7", "s_12", "s_15", "s_20", "s_21"}
    assert all(both["hpc_degradation"][s] == -both["fan_degradation"][s] for s in both["fan_degradation"])
    assert discriminating(PACK, ["hpc_degradation"]) == {"hpc_degradation": {}}


def test_findings_cite_their_evidence_and_stay_in_range(snapshot):
    ledger, a, b, drivers, modes, _ = _run(snapshot, "FD003-A100")
    assert [a.method, b.method] == ["lightgbm", "health_index"]
    for finding in [a, b, *drivers, *modes]:
        assert finding.evidence_id in ledger.records
    for rul in (a, b):
        assert 0 <= rul.lower <= rul.rul <= rul.upper <= 125
    assert all(ev.kind == "model" and ev.produced_by == "prognostics" for ev in ledger.records.values())
    assert abs(sum(m.probability for m in modes) - 1) < 0.01


def test_shap_contributions_add_up_to_the_prediction(snapshot):
    ledger, a, _, drivers, _, _ = _run(snapshot, "FD001-A031")
    shap = ledger.records[drivers[0].evidence_id].content
    total = shap["base_value"] + shap["age_contribution"] + sum(shap["by_sensor"].values())
    assert total == pytest.approx(shap["raw_prediction"], abs=0.1)
    assert shap["raw_prediction"] == pytest.approx(a.rul, abs=0.5)
    assert all(d.contribution < 0 for d in drivers)
    assert drivers[0].component == "hpc"


def test_healthy_engine_reports_no_trend(snapshot):
    ledger, a, b, _, modes, anomaly = _run(snapshot, "FD001-A001")
    assert b.at_cap and "healthy" in b.note
    assert ledger.records[b.evidence_id].content["degradation_detected"] is False
    assert a.rul > 100
    assert [m.failure_mode for m in modes] == ["hpc_degradation"]
    assert anomaly.flagged is False
    assert anomaly.evidence_id in ledger.records


def test_fd003_engine_gets_both_candidate_modes(snapshot):
    *_, modes, _ = _run(snapshot, "FD003-A100")
    assert {m.failure_mode for m in modes} == {"hpc_degradation", "fan_degradation"}
