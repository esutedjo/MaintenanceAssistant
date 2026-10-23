import json

import pytest

from agents.intake import parse
from graph.runner import run_case
from run import handle
from settings import ROOT, Settings
from tools.model_tools import ModelTools


@pytest.fixture
def settings(tmp_path):
    return Settings(sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def _trace(result: dict) -> list[dict]:
    lines = (ROOT / result["trace_file"]).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_parse_reads_asset_esn_and_goal():
    assert parse("How long can fd003-a100 keep flying?").model_dump(include={"goal", "asset_id"}) == {
        "goal": "remaining_life", "asset_id": "FD003-A100"}
    assert parse("What is wrong with ESN 700400?").asset_id == "ESN-700400"
    assert parse("What should I do about FD001-A031, how long has it got?").goal == "full_assessment"
    assert parse("What is the status of FD001-A001?").goal == "status"
    assert parse("Is the engine ok?").needs_clarification


def test_full_run_cites_resolvable_evidence(settings):
    result = run_case("How long can FD003-A100 keep flying and what is wrong with it?", settings)
    assert result["status"] == "needs_review"
    assert result["mode"] == {"sample_mode": True, "data": "snapshot", "context_store": "sqlite"}
    claims = result["findings"]["claims"]
    assert claims and all(i in result["evidence"] for c in claims for i in c["evidence_ids"])
    assert result["findings"]["rul_ml"]["evidence_id"] == "EV-PRG-01"
    assert len(result["findings"]["similar_cases"]) == 3
    assert result["recommendation"] and result["recommendation"]["deadline"]
    trace = _trace(result)
    agents = {e["agent_name"] for e in trace}
    assert {"intake", "planner", "data", "prognostics", "diagnostics", "brief",
            "maint_planner", "rule_checks", "critic", "escalation"} <= agents
    assert all(e["status"] in {"success", "needs_revision"} for e in trace)


def test_critical_engine_recommends_lease_swap(settings):
    result = run_case("What should I do about FD001-A031?", settings)
    rec = result["recommendation"]
    assert "FM-HPC-06" in rec["task_ids"]
    assert rec["action"].lower().startswith("swap")
    assert any(p["part_number"] == "TF-ENG-9001" for p in rec["parts"])
    assert any(e["rule"] == "rul_below_lead_time" for e in result["escalations"])


def test_fan_case_swaps_when_lead_time_exceeds_rul(settings):
    result = run_case("How long can FD003-A100 keep flying and what is wrong with it?", settings)
    rec = result["recommendation"]
    assert "FM-FAN-04" in rec["task_ids"]
    assert rec["action"].lower().startswith("swap")
    assert any(e["rule"] == "rul_below_lead_time" for e in result["escalations"])


def test_healthy_engine_skips_similar_cases(settings):
    result = run_case("What is the status of FD001-A001?", settings)
    assert result["status"] == "needs_review"
    assert result["findings"]["similar_cases"] == []
    assert result["findings"]["rul_trend"]["at_cap"]


@pytest.mark.parametrize("question", ["Is the engine ok?", "How long can FD003-A999 keep flying?"])
def test_missing_or_unknown_asset_asks_back(settings, question):
    result = run_case(question, settings)
    assert result["status"] == "needs_clarification"
    assert result["intent"]["clarification"]
    assert [e["agent_name"] for e in _trace(result)] == ["intake"]


def test_failing_specialist_ends_in_error_with_trace(settings, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("model file corrupt")

    monkeypatch.setattr(ModelTools, "drivers", broken)
    result = run_case("How long can FD003-A100 keep flying?", settings)
    assert result["status"] == "error"
    statuses = {e["agent_name"]: e["status"] for e in _trace(result)}
    assert statuses["prognostics"] == "error" and statuses["brief"] == "error"
    assert "escalation" not in statuses
    assert result["findings"]["claims"] == []


@pytest.mark.parametrize("payload", [None, {}, {"question": ""}, {"question": 42}])
def test_run_rejects_bad_input_with_structured_error(payload):
    status, body = handle(payload)
    assert status == 400
    assert body["status"] == "error" and body["error"]["code"] == "invalid_input"
