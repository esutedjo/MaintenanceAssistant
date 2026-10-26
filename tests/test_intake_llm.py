"""Nemotron reads the engine from free text only when the parser cannot, and code checks what it says."""

import json

import pytest

from agents.intake import grounded_engine, numbers_in
from casefile.context_store import SqliteStore
from casefile.load_context import load as load_context
from graph.runner import run_case
from settings import ROOT, Settings
from tools.llm import LlmClient, LlmResponse

MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


def _settings(tmp_path, **overrides) -> Settings:
    values = dict(_env_file=None, sample_mode=False, zetaris_mode="snapshot", context_store="sqlite",
                  context_sqlite_path=tmp_path / "ctx.sqlite", meterless_base_url="http://127.0.0.1:9/v1",
                  meterless_runtime_token="test-token", critic_timeout_s=2.0)
    values.update(overrides)
    settings = Settings(**values)
    store = SqliteStore(settings.context_sqlite_path)
    load_context(store, settings.model_copy(update={"zetaris_mode": "snapshot"}))
    store.close()
    return settings


def _nightshift(monkeypatch, intake_reply: str, calls: list | None = None) -> None:
    def chat(self, messages, **kwargs):
        prompt = messages[0]["content"]
        if calls is not None:
            calls.append(prompt[:20])
        text = intake_reply if prompt.startswith("You map") else '{"outcome": "accept", "gaps": []}'
        return LlmResponse(text=text, provider="meterless", requested_model="meterless/auto", model=MODEL,
                           duration_s=0.1)

    monkeypatch.setattr(LlmClient, "chat", chat)


def test_numbers_in_reads_digits_words_and_ordinals():
    assert {31, 1} <= numbers_in("engine thirty-one of the first fleet")
    assert 700131 in numbers_in("serial 700131")
    assert {1, 31} <= numbers_in("FD001-A31")


@pytest.mark.parametrize("question, reply, expected", [
    ("Should we pull the engine with serial 700131?", {"engine": "ESN-700131", "quote": "serial 700131"},
     "ESN-700131"),
    ("How is engine thirty-one of the FD001 fleet?", {"engine": "fd001-a031", "quote": "engine thirty-one"},
     "FD001-A031"),
    ("Is FD001-A31 ok?", {"engine": "FD001-A031", "quote": "FD001-A31"}, "FD001-A031"),
])
def test_grounded_engine_accepts_what_the_question_says(question, reply, expected):
    assert grounded_engine(question, reply) == (expected, "")


@pytest.mark.parametrize("question, reply, reason", [
    ("Is the bad one ok?", {"engine": "FD001-A031", "quote": "the bad one"}, "unit 31"),
    ("How is engine 31?", {"engine": "FD003-A031", "quote": "engine 31"}, "fleet FD003"),
    ("How is engine 31 of FD001?", {"engine": "FD001-A031", "quote": "engine 32"}, "not in the question"),
    ("Tell me a joke", {"engine": None, "quote": ""}, "no engine"),
    ("How is the engine?", {"engine": "the left one", "quote": "the engine"}, "not an engine ID"),
])
def test_grounded_engine_refuses_a_guess(question, reply, reason):
    engine, why = grounded_engine(question, reply)
    assert engine is None and reason in why


def test_free_text_question_runs_after_nemotron_reads_the_serial(tmp_path, monkeypatch):
    _nightshift(monkeypatch, '{"engine": "ESN-700131", "quote": "serial 700131"}')
    result = run_case("Should we pull the engine with serial 700131?", _settings(tmp_path))
    assert result["status"] == "needs_review" and result["asset"]["asset_id"] == "FD001-A031"
    assert result["intent"]["resolved_by"] == MODEL
    lines = [json.loads(x) for x in (ROOT / result["trace_file"]).read_text(encoding="utf-8").splitlines()]
    intake = next(e for e in lines if e["agent_name"] == "intake" and e["action"] == "parse_and_resolve")
    assert intake["model"] == MODEL and "checked against the question and registry" in intake["output_summary"]
    assert intake["recorded"] is False, "a live call says recorded: false"
    assert result["model_calls"][0] == {"agent": "intake", "model": MODEL, "recorded": False,
                                        "source": "live Nightshift call"}


def test_an_ungrounded_answer_asks_back(tmp_path, monkeypatch):
    _nightshift(monkeypatch, '{"engine": "FD001-A031", "quote": "the bad one"}')
    result = run_case("Is the bad one ok?", _settings(tmp_path))
    assert result["status"] == "needs_clarification" and result["intent"]["resolved_by"] is None


def test_fleet_wording_is_read_but_a_real_fleet_question_keeps_the_fleet_answer(tmp_path, monkeypatch):
    _nightshift(monkeypatch, '{"engine": "FD001-A031", "quote": "engine thirty-one"}')
    one = run_case("How is engine thirty-one of the first fleet doing?", _settings(tmp_path))
    assert one["asset"]["asset_id"] == "FD001-A031"
    _nightshift(monkeypatch, '{"engine": null, "quote": ""}')
    fleet = run_case("Which engines need attention this week?", _settings(tmp_path))
    assert fleet["status"] == "needs_clarification" and "Fleet tab" in fleet["intent"]["clarification"]


def test_parser_hits_and_sample_mode_never_call_the_model(tmp_path, monkeypatch):
    calls: list = []
    _nightshift(monkeypatch, '{"engine": null, "quote": ""}', calls)
    run_case("What should I do about FD001-A031?", _settings(tmp_path))
    assert not [c for c in calls if c.startswith("You map")]
    run_case("Should we pull the engine with serial 700131?", _settings(tmp_path, sample_mode=True))
    assert not [c for c in calls if c.startswith("You map")]
