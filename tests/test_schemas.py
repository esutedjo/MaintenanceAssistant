import json

import pytest
from pydantic import ValidationError

from casefile.schemas import CaseFile, Claim, Evidence, EvidenceLedger, TraceEvent, append, merge_evidence
from casefile.trace import TraceWriter


def _evidence(id_: str, value: int = 1) -> Evidence:
    return Evidence(id=id_, kind="query", source="snapshot:x", summary="s", content={"v": value}, produced_by="data")


def test_evidence_id_must_carry_agent_code():
    assert _evidence("EV-DAT-01").id == "EV-DAT-01"
    for bad in ["EV-007", "EV-dat-01", "EV-DAT-1", "DAT-01"]:
        with pytest.raises(ValidationError):
            _evidence(bad)


def test_claim_without_evidence_is_invalid():
    with pytest.raises(ValidationError):
        Claim(text="RUL is 40 cycles", evidence_ids=[], confidence="high")


def test_ledger_allocates_after_existing_ids():
    ledger = EvidenceLedger("data", existing={"EV-DAT-01": _evidence("EV-DAT-01"), "EV-PRG-05": _evidence("EV-PRG-05")})
    first = ledger.add("query", "snapshot:cycles.parquet", "s", {})
    second = ledger.add("query", "snapshot:assets.parquet", "s", {}, degraded="Zetaris down")
    assert [first.id, second.id] == ["EV-DAT-02", "EV-DAT-03"]
    assert second.degraded == "Zetaris down"
    assert set(ledger.records) == {"EV-DAT-02", "EV-DAT-03"}


def test_merge_evidence_combines_parallel_branches_and_refuses_rewrites():
    merged = merge_evidence({"EV-DAT-01": _evidence("EV-DAT-01")}, {"EV-PRG-01": _evidence("EV-PRG-01")})
    assert set(merged) == {"EV-DAT-01", "EV-PRG-01"}
    assert merge_evidence(merged, {"EV-DAT-01": _evidence("EV-DAT-01")}) == merged
    with pytest.raises(ValueError, match="rewritten"):
        merge_evidence(merged, {"EV-DAT-01": _evidence("EV-DAT-01", value=2)})
    assert append([1], [2, 3]) == [1, 2, 3]


def test_case_file_reports_unresolved_ids_and_caps_rounds():
    case = CaseFile(case_id="c1", question="q", evidence={"EV-DAT-01": _evidence("EV-DAT-01")})
    assert case.unresolved(["EV-DAT-01", "EV-PRG-09"]) == ["EV-PRG-09"]
    with pytest.raises(ValidationError):
        CaseFile(case_id="c1", question="q", round=4)


def test_trace_writer_appends_jsonl(tmp_path):
    writer = TraceWriter("run-1", log_dir=tmp_path)
    for action in ["plan", "query"]:
        writer.write(TraceEvent(run_id="run-1", agent_name="data", action=action, input_summary="i",
                                output_summary="o", model="meterless/auto"))
    lines = (tmp_path / "trace-run-1.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert [r["action"] for r in rows] == ["plan", "query"]
    expected = {"timestamp", "run_id", "agent_name", "action", "input_summary", "output_summary", "target_agent",
                "model", "confidence", "retry_count", "status"}
    assert expected <= set(rows[0])
