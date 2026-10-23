from datetime import date

import pytest

from assurance.escalation import run_escalations
from assurance.rules import mandates_honoured
from casefile.schemas import (
    AssetRef, CaseFile, Claim, DocumentFinding, Evidence, Intent, Recommendation,
)
from data.db import load_pack
from graph.runner import run_case
from settings import Settings
from tools.data_access import SnapshotSource
from tools.knowledge import embed, in_effect, load_chunks, mandate_applies, retrieve

PACK = load_pack()
ON = date(2026, 10, 22)
AD = {"failure_modes": ["hpc_degradation"], "min_probability": 0.5, "rul_lower_below": 30, "task": "FM-HPC-01",
      "within_cycles": 10, "action": "Borescope"}


@pytest.fixture(scope="module")
def chunks():
    return load_chunks(PACK)


def test_every_section_becomes_an_embedded_chunk(chunks):
    assert len(chunks) == 13
    assert {c["doc_type"] for c in chunks} == {"manual", "service_bulletin", "airworthiness_directive"}
    assert all(len(c["embedding"]) == 512 for c in chunks)
    assert embed(["borescope HPC"]).shape == (1, 512)


def test_snapshot_matches_documents(chunks):
    snap = SnapshotSource().doc_chunks("turbofan_cmapss").frame
    assert sorted(snap.chunk_id) == sorted(c["chunk_id"] for c in chunks)


def test_superseded_and_future_revisions_are_not_in_effect():
    assert in_effect({"effective_date": "2025-06-01", "superseded_by": "AMM-TF-R3", "superseded_on": "2026-03-01"}, ON)
    assert in_effect({"effective_date": "2026-11-01", "superseded_by": ""}, ON)
    assert in_effect({"effective_date": "2026-03-01", "superseded_by": ""}, ON) is None


def test_mandate_needs_every_condition():
    assert mandate_applies(AD, [("hpc_degradation", 1.0)], 5.0)
    assert mandate_applies(AD, [("hpc_degradation", 1.0)], 117.0) is None
    assert mandate_applies(AD, [("hpc_degradation", 0.3)], 5.0) is None
    assert mandate_applies(AD, [("fan_degradation", 1.0)], 5.0) is None


def test_hpc_retrieval_applies_precedence_and_excludes_old_revision(chunks):
    found = retrieve(chunks, PACK, variant="FD001", fault_modes=[("hpc_degradation", 1.0)], rul_lower=0.0, on=ON)
    labels = [r.label for r in found.applicable]
    assert labels[0] == "AD-2026-17-04 (g)" and found.applicable[0].mandate_reason
    manual = next(r for r in found.applicable if r.label == "AMM-TF-R3 72-31-00")
    assert manual.overridden_by == "SB-TF-72-0142 3.B"
    assert any(r.label == "AMM-TF-R2 72-31-00" and "superseded" in r.excluded for r in found.excluded)


def test_fan_retrieval_skips_fd001_only_documents_and_hpc_mandate(chunks):
    found = retrieve(chunks, PACK, variant="FD003", fault_modes=[("fan_degradation", 1.0)], rul_lower=2.9, on=ON)
    assert found.applicable and not found.mandates
    assert all("fan" in r.chunk["components"] or "engine" in r.chunk["components"] for r in found.applicable)


def _mandate_case(task_ids: list[str], cite: bool, deadline: date) -> CaseFile:
    doc = DocumentFinding(doc_id="AD-2026-17-04", doc_type="airworthiness_directive", section="(g)", heading="h",
                          revision="0", effective_date="2026-09-01", precedence=0, relevance=0.2, mandatory=True,
                          required_task="FM-HPC-01", within_cycles=10, action="Borescope", evidence_id="EV-KNW-02")
    ev = Evidence(id="EV-KNW-02", kind="document", source="t", summary="s", content={}, produced_by="knowledge")
    rec = Recommendation(action="Swap", task_ids=task_ids, deadline=deadline,
                         rationale=[Claim(text="x", evidence_ids=["EV-KNW-02" if cite else "EV-PRG-01"],
                                          confidence="high")], confidence="high")
    return CaseFile(case_id="c", question="q", asset=AssetRef(asset_id="FD001-A031", asset_type="turbofan_cmapss",
                                                             pack_version="0.1.0", variant="FD001"),
                    intent=Intent(goal="full_assessment", asset_id="FD001-A031"), evidence={"EV-KNW-02": ev},
                    findings={"documents": [doc]}, recommendation=rec, round=1)


def test_rule_check_blocks_dropping_or_deferring_a_mandate():
    assert mandates_honoured(_mandate_case(["FM-HPC-01"], True, date(2026, 10, 25)), PACK, ON) == []
    assert mandates_honoured(_mandate_case(["FM-HPC-06"], True, date(2026, 10, 25)), PACK, ON)
    assert mandates_honoured(_mandate_case(["FM-HPC-01"], False, date(2026, 10, 25)), PACK, ON)
    assert mandates_honoured(_mandate_case(["FM-HPC-01"], True, date(2026, 11, 30)), PACK, ON)


def test_mandate_escalation_is_listed_first():
    items = run_escalations(_mandate_case(["FM-HPC-01"], True, date(2026, 10, 25)), PACK)
    assert items[0].rule == "document_mandate" and items[0].severity == "urgent"


def test_graph_keeps_mandated_task_next_to_engine_swap(tmp_path):
    result = run_case("What should I do about FD001-A031?", Settings(sample_mode=True,
                                                                     context_sqlite_path=tmp_path / "c.sqlite"))
    rec = result["recommendation"]
    assert rec["action"].lower().startswith("swap") and "FM-HPC-01" in rec["task_ids"]
    assert result["escalations"][0]["rule"] == "document_mandate"
    assert any(e["kind"] == "document" for e in result["evidence"].values())
