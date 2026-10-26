"""Recorded Nemotron replies in sample mode: used only when the prompt hash matches exactly, labelled as recorded in
the trace, the decision log and the run result, and checked by code exactly like a live reply."""

import json

import pytest

from agents.critic import critic_prompt
from agents.intake import intake_prompt
from casefile.decision_log import derive_log
from data.db import load_pack
from graph.runner import execute_run
from settings import ROOT, Settings
from tools import llm_recordings
from tools.record_llm import FREE_TEXT, golden_questions

PACK = load_pack()
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
HPC = "What should I do about FD001-A031?"
COMMITTED = ROOT / "data" / "snapshot" / "llm_recordings.json"


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def _write(path, prompt, text, purpose="critic"):
    llm_recordings.save([llm_recordings.Recording(
        purpose=purpose, case="test", prompt_sha256=llm_recordings.prompt_hash(prompt), model=MODEL,
        captured_at="2026-10-26T10:00:00Z", text=text)], path)


def test_a_recorded_reply_is_used_labelled_and_checked_by_code(settings, tmp_path, monkeypatch):
    plain = execute_run(HPC, settings).case                            # no recordings: deterministic only
    assert plain.verdicts[-1].model is None
    path = tmp_path / "recordings.json"
    _write(path, critic_prompt(plain, PACK),
           '{"outcome": "revise", "gaps": ["slot not cited"], "challenges": [{"kind": "missing_mandate", '
           '"claim": null, "value": null, "detail": "AD task?"}]}')
    monkeypatch.setattr(llm_recordings, "RECORDINGS", path)
    outcome = execute_run(HPC, settings)
    case, result = outcome.case, outcome.result
    verdict = case.verdicts[-1]
    label = "recorded Nemotron response (captured 2026-10-26)"
    assert (verdict.model, verdict.recorded, verdict.outcome) == (MODEL, label, "accept")
    assert any(g.startswith("missing mandate challenge refuted by code") for g in verdict.gaps)
    critic = next(e for e in case.trace if e.agent_name == "critic" and e.action == "disprove")
    assert critic.model == MODEL and label in critic.output_summary and critic.recorded is True
    line = next(json.loads(row) for row in (ROOT / result["trace_file"]).read_text(encoding="utf-8").splitlines()
                if json.loads(row)["action"] == "disprove")
    assert line["model"] == MODEL and line["recorded"] is True, "the JSONL line says it was a replay"
    assert not any("recorded" in json.loads(row) for row in (ROOT / result["trace_file"]).read_text(
        encoding="utf-8").splitlines() if not json.loads(row).get("model")), "code steps carry no recorded field"
    review = next(e for e in derive_log(case) if e.actor == MODEL)
    assert review.decision == "reviewed the brief" and label in review.rationale and review.recorded is True
    refuted = next(e for e in derive_log(case) if e.decision == "challenge refuted")
    assert refuted.recorded is True and label in refuted.rationale
    assert result["model_calls"] == [{"agent": "critic", "round": 1, "model": MODEL, "recorded": True,
                                      "source": label}]


def test_a_changed_prompt_gets_no_recorded_reply(settings, tmp_path, monkeypatch):
    path = tmp_path / "recordings.json"
    _write(path, critic_prompt(execute_run(HPC, settings).case, PACK), '{"outcome": "accept", "gaps": []}')
    monkeypatch.setattr(llm_recordings, "RECORDINGS", path)
    reworded = execute_run("What should I do about FD001-A031 today?", settings).case
    assert reworded.verdicts[-1].model is None and reworded.verdicts[-1].recorded is None


def test_a_recorded_intake_reply_is_grounded_by_code(settings, tmp_path, monkeypatch):
    path = tmp_path / "recordings.json"
    _write(path, intake_prompt(FREE_TEXT), '{"engine": "ESN-700131", "quote": "serial 700131"}', purpose="intake")
    monkeypatch.setattr(llm_recordings, "RECORDINGS", path)
    case = execute_run(FREE_TEXT, settings).case
    assert case.status == "needs_review" and case.asset.asset_id == "FD001-A031"
    assert case.intent.resolved_by == MODEL and case.intent.recorded.startswith("recorded Nemotron response")
    read = next(e for e in case.trace if e.action == "parse_and_resolve")
    assert read.model == MODEL and read.recorded is True and "(captured 2026-10-26)" in read.output_summary
    _write(path, intake_prompt(FREE_TEXT), '{"engine": "ESN-700131", "quote": "the bad one"}', purpose="intake")
    asked = execute_run(FREE_TEXT, settings).case
    assert asked.status == "needs_clarification"                                      # quote not in the question
    back = next(e for e in asked.trace if e.action == "parse_and_resolve")
    assert back.model == MODEL and back.recorded is True, "an ask-back from a replay never reads as a live call"
    assert next(e for e in derive_log(asked) if e.actor == MODEL).recorded is True


@pytest.mark.skipif(not COMMITTED.exists(), reason="no recordings captured (uv run task record-llm)")
def test_golden_prompts_still_match_the_committed_recordings(settings, monkeypatch):
    """A change to a prompt, a claim or an evidence summary changes the hash: re-run `uv run task record-llm`."""
    monkeypatch.setattr(llm_recordings, "RECORDINGS", COMMITTED)
    for question in golden_questions():
        case = execute_run(question, settings).case
        last = case.verdicts[-1]
        assert case.status == "needs_review", question
        assert last.gate == "critic" and last.recorded and last.model, (
            f"no recorded reply matched the critic prompt for {question!r}; re-run uv run task record-llm")
    intake = execute_run(FREE_TEXT, settings).case
    assert intake.intent.recorded, "the free-text intake prompt no longer matches its recording"
    rows = json.loads(COMMITTED.read_text(encoding="utf-8"))["recordings"]
    assert rows and all(set(r) == {"purpose", "case", "prompt_sha256", "model", "captured_at", "text"} for r in rows)
