import json
from datetime import date

import pytest

import tools.writeback as writeback
from casefile.context_store import MemoryIn, SqliteStore
from casefile.decision_log import Signer, brief_ref, derive_log, verify_log
from casefile.schemas import AssetRef, CaseFile, Claim, Evidence, FaultModeFinding, Findings, Recommendation, RulFinding
from graph.runner import execute_run
from settings import Settings
from tools.writeback import approve_case, cited_cases, reject_case

SIGNER = Signer(name="A. Engineer", role="B1 engineer", licence_ref="TEST-0001")


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
    record = approve_case(_case(), settings, comment="go", path=path, signer=SIGNER)
    assert record["wo_id"] == "WO-DRAFT-20261023T000000Z-abc123"
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
    record = reject_case(_case(), settings, comment="hold", path=path, signer=SIGNER)
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
    record = approve_case(case, settings, path=tmp_path / "wo.jsonl", case_feedback={ids[1]: "wrong"},
                          signer=SIGNER)
    applied = {f["memory_id"]: f["kind"] for f in record["memory"]["case_feedback"]}
    assert applied == {ids[0]: "helpful", ids[1]: "wrong"}
    assert _trust(settings, ids[0])[0] == 0.75
    trust, tags = _trust(settings, ids[1])
    assert trust == 0.5 and "review" in tags


def test_reject_marks_cited_cases_not_helpful(settings, tmp_path):
    case, ids = _case_with_memories(settings)
    record = reject_case(case, settings, path=tmp_path / "wo.jsonl", signer=SIGNER)
    assert {f["kind"] for f in record["memory"]["case_feedback"]} == {"not_helpful"}
    assert _trust(settings, ids[0])[0] == 0.67


def test_cannot_approve_twice(settings, tmp_path):
    case = _case(status="approved")
    with pytest.raises(ValueError, match="approved"):
        approve_case(case, settings, path=tmp_path / "x.jsonl", signer=SIGNER)


def test_approve_attaches_the_signed_decision_log_to_the_work_order(settings, tmp_path):
    case = execute_run("How long can FD003-A100 keep flying and what is wrong with it?", settings).case
    path = tmp_path / "draft_work_orders.jsonl"
    record = approve_case(case, settings, comment="swap tonight", path=path, signer=SIGNER, acknowledged=[0],
                          reason=" Lease engine on site ")
    log = record["decision_log"]
    human = log["entries"][-1]
    assert record["signed_by"] == {"name": "A. Engineer", "role": "B1 engineer", "licence_ref": "TEST-0001"}
    assert (human["actor_type"], human["actor"], human["decision"]) == ("human", "A. Engineer", "approved")
    assert [(a["rule"], a["severity"], a["reason"]) for a in human["acknowledged"]] == [
        ("rul_below_lead_time", "urgent", "Lease engine on site")]
    assert human["comment"] == "swap tonight" and human["wo_id"] == record["wo_id"]
    assert human["brief"] == record["brief"] == brief_ref(case).model_dump()
    assert len(human["case_feedback"]) == 3 and set(human["case_feedback"].values()) == {"helpful"}
    assert [e["decision"] for e in log["entries"][:-1]] == [e.decision for e in derive_log(case)]
    assert "not a certificate of release to service" in record["release_note"]
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["decision_log"]["entries"] == log["entries"] and stored["signed_by"]["name"] == "A. Engineer"
    file = tmp_path / "decision_logs" / f"{case.case_id}.jsonl"
    assert log["path"].endswith(f"{case.case_id}.jsonl")
    assert verify_log(file, log["head_hash"]) == (True, f"hash chain intact, {len(log['entries'])} entries")


def test_reject_records_the_signer_and_ratings_in_the_log(settings, tmp_path):
    case = execute_run("What should I do about FD001-A031?", settings).case
    marked = cited_cases(case)[0]["memory_id"]
    record = reject_case(case, settings, comment="borescope first", path=tmp_path / "wo.jsonl",
                         case_feedback={marked: "wrong"}, signer="  B. Inspector ")
    human = record["decision_log"]["entries"][-1]
    assert (human["actor"], human["decision"], human["comment"]) == ("B. Inspector", "rejected", "borescope first")
    assert human["signer"] == {"name": "B. Inspector", "role": None, "licence_ref": None}
    assert sorted(human["case_feedback"].values()) == ["not_helpful", "not_helpful", "wrong"]
    assert record["status"] == "rejected" and "no work order" in record["release_note"]
    assert verify_log(tmp_path / "decision_logs" / f"{case.case_id}.jsonl", record["decision_log"]["head_hash"])[0]


def test_approve_needs_every_escalation_acknowledged_and_reject_does_not(settings, tmp_path):
    case = execute_run("What should I do about FD001-A031?", settings).case
    assert [e.rule for e in case.escalations] == ["document_mandate", "rul_below_lead_time", "sb_adoption"]
    with pytest.raises(ValueError, match="not acknowledged: rul_below_lead_time"):
        approve_case(case, settings, path=tmp_path / "wo.jsonl", signer=SIGNER, acknowledged=[0])
    assert not (tmp_path / "wo.jsonl").exists() and not (tmp_path / "decision_logs").exists()
    record = reject_case(case, settings, path=tmp_path / "wo.jsonl", signer=SIGNER, acknowledged=[0])
    assert [a["rule"] for a in record["decision_log"]["entries"][-1]["acknowledged"]] == ["document_mandate"]


def test_approve_despite_an_urgent_escalation_needs_a_reason_and_reject_does_not(settings, tmp_path):
    case = execute_run("How long can FD003-A100 keep flying and what is wrong with it?", settings).case
    assert [(e.rule, e.severity) for e in case.escalations] == [("rul_below_lead_time", "urgent"),
                                                                ("sb_adoption", "info")]
    with pytest.raises(ValueError, match="needs a reason; urgent: rul_below_lead_time"):
        approve_case(case, settings, path=tmp_path / "wo.jsonl", signer=SIGNER, acknowledged=[0], reason="  ")
    assert not (tmp_path / "wo.jsonl").exists() and not (tmp_path / "decision_logs").exists()
    record = reject_case(case, settings, path=tmp_path / "wo.jsonl", signer=SIGNER, acknowledged=[0])
    assert record["decision_log"]["entries"][-1]["acknowledged"][0]["reason"] is None
    assert verify_log(tmp_path / "decision_logs" / f"{case.case_id}.jsonl", record["decision_log"]["head_hash"])[0]


@pytest.mark.parametrize("sign", [approve_case, reject_case])
def test_sign_off_without_a_signer_is_refused(settings, tmp_path, sign):
    with pytest.raises(ValueError, match="signer"):
        sign(_case(), settings, path=tmp_path / "wo.jsonl", signer="   ")
    assert not (tmp_path / "wo.jsonl").exists() and not (tmp_path / "decision_logs").exists()


def test_one_sign_off_per_case_even_from_parallel_threads(settings, tmp_path):
    """Eight approvals at once: one succeeds, seven are refused, one draft, and the chain still verifies. A later
    reject is refused too unless it is an explicit correction."""
    import threading

    case, path = _case(), tmp_path / "wo.jsonl"
    results, start = [], threading.Barrier(8)

    def sign():
        start.wait()
        try:
            results.append(approve_case(case, settings, path=path, signer=SIGNER))
        except ValueError as error:
            results.append(error)

    threads = [threading.Thread(target=sign) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    records = [r for r in results if isinstance(r, dict)]
    assert len(records) == 1 and all("already signed" in str(r) for r in results if not isinstance(r, dict))
    assert path.read_text(encoding="utf-8").count("\n") == 1
    log = tmp_path / "decision_logs" / f"{case.case_id}.jsonl"
    assert verify_log(log, records[0]["decision_log"]["head_hash"])[0]
    with pytest.raises(ValueError, match="already signed .approved by A. Engineer."):
        reject_case(case, settings, path=path, signer="M. Allory")
    fixed = reject_case(case, settings, path=path, signer="B. Engineer", correction=True)
    human = fixed["decision_log"]["entries"][-1]
    assert human["rationale"].startswith("Correction of the earlier sign-off.")
    assert verify_log(log, fixed["decision_log"]["head_hash"])[0]
    assert not (tmp_path / "decision_logs" / f"{case.case_id}.lock").exists(), "the lock is released"


class _Connection:
    def __init__(self, calls: list):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def commit(self):
        pass


def test_live_mode_keeps_the_cmms_columns_and_writes_the_record_with_the_log(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(writeback.psycopg, "connect", lambda **kwargs: _Connection(calls))
    live = Settings(_env_file=None, sample_mode=False, pg_app_user="app_rw", context_store="sqlite",
                    context_sqlite_path=tmp_path / "context.sqlite")
    record = approve_case(_case(), live, path=tmp_path / "wo.jsonl", signer=SIGNER)
    sql, params = calls[0]
    assert ("(wo_id, asset_id, wo_type, status, opened_at, closed_at, cycle_at, component, description, origin)"
            in sql) and len(params) == 9 and params[3] == "draft"
    stored = json.loads((tmp_path / "wo.jsonl").read_text(encoding="utf-8"))
    assert record["backend"] == stored["backend"] == "cmms"
    assert stored["decision_log"]["head_hash"] == record["decision_log"]["head_hash"]
    assert stored["signed_by"]["name"] == "A. Engineer"
