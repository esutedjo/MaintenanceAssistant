from datetime import date

import pytest

from casefile.context_store import MemoryIn, SqliteStore
from casefile.schemas import AssetRef, CaseFile, Claim, Evidence, FaultModeFinding, Findings, Recommendation, RulFinding
from settings import Settings
from tools.writeback import approve_case, reject_case


def _case(**kwargs) -> CaseFile:
    evidence = {
        "EV-PRG-01": Evidence(id="EV-PRG-01", kind="model", source="model:rul_a", summary="RUL A",
                              content={"last_cycle": 180}, produced_by="prognostics"),
        "EV-DIA-01": Evidence(id="EV-DIA-01", kind="model", source="model:fault", summary="HPC",
                              content={"failure_mode": "hpc_degradation"}, produced_by="diagnostics"),
    }
    defaults = dict(
        case_id="20261023T000000Z-abc123",
        question="What should I do about FD001-A031?",
        asset=AssetRef(asset_id="FD001-A031", asset_type="turbofan_cmapss", pack_version="0.1.0", variant="FD001"),
        findings=Findings(
            rul_ml=RulFinding(method="lightgbm", rul=10, lower=5, upper=20, at_cap=False, evidence_id="EV-PRG-01"),
            fault_modes=[FaultModeFinding(failure_mode="hpc_degradation", probability=0.9, evidence_id="EV-DIA-01")],
        ),
        recommendation=Recommendation(
            action="Swap in lease engine; module shop visit on the removed engine",
            task_ids=["FM-HPC-06"],
            deadline=date(2026, 10, 25),
            rationale=[Claim(text="RUL below lead time", evidence_ids=["EV-PRG-01"], confidence="high")],
            confidence="high",
        ),
        evidence=evidence,
        status="needs_review",
    )
    defaults.update(kwargs)
    return CaseFile(**defaults)


@pytest.fixture
def settings(tmp_path):
    return Settings(sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def test_approve_writes_jsonl_in_sample_mode(settings, tmp_path):
    path = tmp_path / "draft_work_orders.jsonl"
    record = approve_case(_case(), settings, comment="go", path=path)
    assert record["wo_id"] == "WO-DRAFT-abc123"
    assert record["backend"] == "file"
    assert record["status"] == "draft"
    assert record["wo_type"] == "engine_swap"
    assert record["component"] == "hpc"
    assert record["cycle_at"] == 180
    assert record["origin"] == "agent"
    assert path.read_text(encoding="utf-8").count("\n") == 1
    assert record["memory"]["ok"] is True


def test_reject_does_not_claim_cmms(settings, tmp_path):
    path = tmp_path / "draft_work_orders.jsonl"
    record = reject_case(_case(), settings, comment="hold", path=path)
    assert record["status"] == "rejected"
    assert record["backend"] == "file"
    assert record["decision"] == "rejected"
    assert "hold" in path.read_text(encoding="utf-8")


def _case_with_memories(settings) -> tuple[CaseFile, list[str]]:
    store = SqliteStore(settings.context_sqlite_path)
    ids = store.write_memories([
        MemoryIn(content=f"HPC removal {i}", source=f"seed:{i}", tags=["case", "hpc_degradation"], confidence=0.7)
        for i in range(2)
    ])["ids"]
    store.close()
    base = _case()
    evidence = dict(base.evidence)
    for n, memory_id in enumerate(ids, start=2):
        evidence[f"EV-DIA-0{n}"] = Evidence(id=f"EV-DIA-0{n}", kind="case", source=f"sqlite:seed:{n}",
                                            summary=f"HPC removal {n}", produced_by="diagnostics",
                                            content={"memory_id": memory_id, "trust": 0.7})
    findings = base.findings.model_copy(update={"similar_cases": ["EV-DIA-02", "EV-DIA-03"]})
    return base.model_copy(update={"evidence": evidence, "findings": findings}), ids


def _trust(settings, memory_id):
    store = SqliteStore(settings.context_sqlite_path)
    row = store._db.execute("SELECT confidence, tags FROM memories WHERE id = ?", (memory_id,)).fetchone()
    store.close()
    return round(row["confidence"], 3), row["tags"]


def test_approve_marks_cited_cases_helpful_unless_engineer_overrides(settings, tmp_path):
    case, ids = _case_with_memories(settings)
    record = approve_case(case, settings, path=tmp_path / "wo.jsonl", case_feedback={ids[1]: "wrong"})
    applied = {f["memory_id"]: f["kind"] for f in record["memory"]["case_feedback"]}
    assert applied == {ids[0]: "helpful", ids[1]: "wrong"}
    assert _trust(settings, ids[0])[0] == 0.75
    trust, tags = _trust(settings, ids[1])
    assert trust == 0.5 and "review" in tags


def test_reject_marks_cited_cases_not_helpful(settings, tmp_path):
    case, ids = _case_with_memories(settings)
    record = reject_case(case, settings, path=tmp_path / "wo.jsonl")
    assert {f["kind"] for f in record["memory"]["case_feedback"]} == {"not_helpful"}
    assert _trust(settings, ids[0])[0] == 0.67


def test_cannot_approve_twice(settings, tmp_path):
    case = _case(status="approved")
    with pytest.raises(ValueError, match="approved"):
        approve_case(case, settings, path=tmp_path / "x.jsonl")
