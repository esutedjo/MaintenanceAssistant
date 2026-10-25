"""World Model is the runtime source of eligible work (features 1–7)."""

from datetime import date

from agents.critic import drivers_vs_fault
from assurance.escalation import knowledge_drift
from assurance.rules import world_tasks_eligible
from casefile.context_store import SqliteStore, entity_id
from casefile.load_context import load
from casefile.schemas import (
    AssetRef, CaseFile, Claim, Driver, Evidence, FaultModeFinding, Recommendation, RulFinding,
)
from casefile.world import (
    asset_state, fmea_extra, mandated_for_mode, mode_component, sensor_component, signoff_batch,
    task_ids_for, tasks_addressing,
)
from data.db import load_pack
from settings import Settings
from tools.writeback import approve_case

PACK = load_pack()


def _store(tmp_path):
    settings = Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "ctx.sqlite")
    store = SqliteStore(settings.context_sqlite_path)
    load(store, settings)
    return store, settings


def test_variant_hides_fan_tasks_on_fd001(tmp_path):
    store, _ = _store(tmp_path)
    hpc = task_ids_for(store, PACK, "hpc_degradation", "FD001")
    fan = task_ids_for(store, PACK, "fan_degradation", "FD001")
    assert hpc == ["FM-HPC-01", "FM-HPC-02", "FM-HPC-03", "FM-HPC-04", "FM-HPC-05", "FM-HPC-06"]
    assert fan == []
    assert "FM-FAN-04" in task_ids_for(store, PACK, "fan_degradation", "FD003")
    assert mode_component(store, PACK, "fan_degradation", "FD003") == "fan"
    assert mode_component(store, PACK, "fan_degradation", "FD001") is None
    store.close()


def test_planner_tasks_come_from_graph_attrs(tmp_path):
    store, _ = _store(tmp_path)
    tasks = tasks_addressing(store, PACK, "hpc_degradation", "FD001")
    swap = next(t for t in tasks if t["fmea_id"] == "FM-HPC-06")
    assert swap["task_type"] == "engine_change"
    assert any(p["part_number"] == "TF-ENG-9001" for p in swap["parts"])
    store.close()


def test_eligibility_gate_rejects_a_task_off_the_graph():
    rec = Recommendation(action="invent", task_ids=["FM-HPC-99"],
                         rationale=[Claim(text="r", evidence_ids=["EV-MNT-04"], confidence="high")], confidence="high")
    case = CaseFile(
        case_id="c", question="q",
        recommendation=rec,
        evidence={"EV-MNT-04": Evidence(
            id="EV-MNT-04", kind="rule", source="sqlite:world/hpc_degradation", summary="wm",
            content={"failure_mode": "hpc_degradation", "task_ids": ["FM-HPC-01", "FM-HPC-06"]},
            produced_by="maint_planner")},
    )
    assert "FM-HPC-99" in world_tasks_eligible(case)[0]
    rec.task_ids = ["FM-HPC-06"]
    assert world_tasks_eligible(case) == []


def test_fmea_drift_escalates_when_table_has_an_extra_task():
    evidence = Evidence(
        id="EV-MNT-04", kind="rule", source="sqlite:world/hpc_degradation", summary="wm",
        content={"failure_mode": "hpc_degradation", "task_ids": ["FM-HPC-01"],
                 "fmea_drift_extra": ["FM-HPC-99"]},
        produced_by="maint_planner")
    case = CaseFile(case_id="c", question="q", evidence={"EV-MNT-04": evidence})
    item = knowledge_drift(case, PACK)
    assert item and item.rule == "knowledge_drift" and "FM-HPC-99" in item.message
    assert fmea_extra(["FM-HPC-01"], ["FM-HPC-01", "FM-HPC-99"]) == ["FM-HPC-99"]


def test_driver_walk_uses_monitors_and_affects(tmp_path):
    store, _ = _store(tmp_path)
    case = CaseFile(
        case_id="c", question="q",
        asset=AssetRef(asset_id="FD003-A100", asset_type="turbofan_cmapss", pack_version="0.1.0", variant="FD003"),
        findings={"fault_modes": [FaultModeFinding(failure_mode="fan_degradation", probability=1.0,
                                                   evidence_id="EV-DIA-01")],
                  "drivers": [Driver(sensor="s_9", component="hpc", contribution=-14.7, evidence_id="EV-PRG-03")]},
        evidence={},
    )
    gap = drivers_vs_fault(case, PACK, store)
    assert gap and "phi" in gap.lower()
    assert "fan" in gap and "hpc" in gap
    assert sensor_component(store, PACK, "s_9") == "hpc"
    store.close()


def test_mandate_edges_name_the_ad_task(tmp_path):
    store, _ = _store(tmp_path)
    mandates = mandated_for_mode(store, PACK, "hpc_degradation", "FD001")
    assert any(m["doc_id"] == "AD-2026-17-04" and m["task"] == "FM-HPC-01" for m in mandates)
    fan = mandated_for_mode(store, PACK, "fan_degradation", "FD001")
    assert fan == []
    store.close()


def test_signoff_writes_facts_on_the_asset(tmp_path):
    store, settings = _store(tmp_path)
    case = CaseFile(
        case_id="20261023T000000Z-abc123", question="q",
        asset=AssetRef(asset_id="FD001-A031", asset_type="turbofan_cmapss", pack_version="0.1.0", variant="FD001"),
        findings={"rul_ml": RulFinding(method="lightgbm", rul=10, lower=0, upper=12, at_cap=False,
                                       evidence_id="EV-PRG-01"),
                  "fault_modes": [FaultModeFinding(failure_mode="hpc_degradation", probability=1.0,
                                                   evidence_id="EV-DIA-01")]},
        recommendation=Recommendation(
            action="Swap in lease engine; module shop visit on the removed engine",
            task_ids=["FM-HPC-06"], deadline=date(2026, 10, 22),
            rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high"),
        evidence={"EV-PRG-01": Evidence(id="EV-PRG-01", kind="model", source="m", summary="s",
                                        content={"last_cycle": 180}, produced_by="prognostics")},
        status="needs_review",
    )
    record = approve_case(case, settings, path=tmp_path / "wo.jsonl")
    assert record["memory"]["world"]["ok"] is True
    state = asset_state(store, PACK, "FD001-A031")
    assert state["last_decision"] == "approved"
    assert state["last_fault"] == "hpc_degradation"
    graph = store.query_entity(entity_id("asset", PACK["asset_type"], "FD001-A031"))
    assert any(e["type"] == "instance_of" for e in graph["outgoing"])
    store.close()
    batch = signoff_batch(PACK, case, "rejected")
    assert batch.facts
