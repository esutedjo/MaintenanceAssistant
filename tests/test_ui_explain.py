"""Ask Cleris to explain: grounded in the brief on screen and the glossary, checked by code, and the glossary answers
when the model cannot. Every model call goes to the mock Nightshift; no real endpoint is called."""

import json
import logging

import httpx
import pytest

from data.db import load_pack
from settings import ROOT, Settings
from tools.llm import LlmClient
from tools.mock_nightshift import mock_nightshift
from ui.explain import advice_in, brief_facts, build_prompt, explain, unsupported_tokens
from ui.help import entry_line, glossary

PACK = load_pack()
TOKEN = "test-runtime-token"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
CASE = json.loads((ROOT / "output_examples" / "critical_hpc.json").read_text(encoding="utf-8"))
GROUNDED = ("Method A estimates about 10 flights (range 5–11) and method B 7. The deadline 2026-10-22 comes from "
            "the planning bound, the cautious end of both. AD-2026-17-04 requires FM-HPC-01 within 10 cycles.")


def _settings(**overrides) -> Settings:
    values = dict(_env_file=None, sample_mode=False, meterless_runtime_token=TOKEN, critic_timeout_s=2.0)
    values.update(overrides)
    return Settings(**values)


def _client(settings: Settings, reply: str = GROUNDED, served_by: str = MODEL, requests: list | None = None):
    transport = mock_nightshift(TOKEN, served_by=served_by, reply=lambda messages: reply, requests=requests)
    return LlmClient(settings, meterless_transport=transport)


def _failing(error: type[httpx.TransportError], requests: list) -> LlmClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise error("no answer", request=request)
    return LlmClient(_settings(), meterless_transport=httpx.MockTransport(handler))


def _grounding(case=CASE) -> str:
    return "\n".join([*brief_facts(case, PACK), *(entry_line(e) for e in glossary(PACK))])


def test_brief_facts_are_the_fields_on_screen():
    facts = "\n".join(brief_facts(CASE, PACK))
    assert "Engine FD001-A031 (FD001 fleet)" in facts
    assert "Do not dispatch; swap in lease engine at the first free slot (2026-10-23)" in facts
    assert "deadline 2026-10-22" in facts and "Colour red" in facts
    assert "Method A (machine learning) flights left: 10  (5–11)" in facts and "Method B (wear trend) flights left: 7" in facts
    assert "Escalation (urgent): Mandatory document action." in facts
    assert "Fault mode HPC degradation: p = 1.00. Evidence EV-DIA-01." in facts
    assert "Planning rules: 2 flights a day" in facts
    assert "signer" not in facts.lower() and "content" not in facts
    assert brief_facts(None, PACK) == []


def test_number_check_accepts_what_the_model_was_given():
    assert unsupported_tokens(GROUNDED, _grounding()) == []
    listed = "1. The fan and HPC are monitored by T30, P30 and phi.\n2. FD001-A031 is in the FD001 fleet (Part-145)."
    assert unsupported_tokens(listed, _grounding()) == []
    assert unsupported_tokens("Method A says 9.8 rounded, about 10 flights.", _grounding()) == ["9.8"]


def test_number_check_flags_invented_numbers_dates_and_ids():
    answer = "It has 42 flights left, due 2026-11-30, per EV-XYZ-99 and SB-TF-72-0999."
    assert unsupported_tokens(answer, _grounding()) == ["2026-11-30", "EV-XYZ-99", "SB-TF-72-0999", "42"]


def test_advice_is_flagged():
    assert advice_in("Method B follows the trend. I recommend swapping the engine now.") == [
        "I recommend swapping the engine now."]
    assert advice_in("You should approve it.") == ["You should approve it."]
    assert advice_in("When you approve, a draft work order is written.") == []


def test_orders_modals_and_number_words_are_flagged_too():
    for text in ("Approve it.", "You must remove the engine today.", "The engineer should reject this brief.",
                 "Best to ground it now."):
        assert advice_in(text) == [text], text
    assert advice_in("Keep in mind the methods differ. You must tick each urgent escalation.") == []
    assert unsupported_tokens("It has about forty flights left, or a dozen days.", _grounding()) == ["forty",
                                                                                                    "dozen"]
    assert unsupported_tokens("Method A estimates about ten flights.", _grounding()) == []


def test_model_answer_goes_through_the_critic_lane_and_is_labelled(caplog):
    settings, seen = _settings(), []
    with caplog.at_level(logging.INFO, logger="explain"):
        result = explain("Why are there two numbers?", CASE, PACK, settings, client=_client(settings, requests=seen))
    assert result.source == "model" and result.text == GROUNDED and result.unsupported == [] and result.advice == []
    assert result.label == (f"AI explanation by {MODEL} via Nightshift; it explains, it does not change the "
                            "decision.")
    request = seen[0]
    assert request.headers["meterless-app"] == settings.meterless_critic_app
    body = json.loads(request.content)
    assert body["model"] == settings.meterless_critic_model and body["temperature"] == 0
    prompt = body["messages"][0]["content"]
    assert "Explain only. Do not recommend actions" in prompt and "Question: Why are there two numbers?" in prompt
    assert "- Engine FD001-A031 (FD001 fleet)." in prompt and "- RUL (remaining useful life): How many" in prompt
    assert f"model={MODEL}" in caplog.text


def test_invented_numbers_and_advice_are_flagged_not_hidden():
    settings = _settings()
    reply = "It can fly 42 more flights. I recommend you approve the swap."
    result = explain("How long?", CASE, PACK, settings, client=_client(settings, reply=reply))
    assert result.source == "model" and result.text == reply
    assert result.unsupported == ["42"] and result.advice == ["I recommend you approve the swap."]


def test_a_lane_that_is_not_nemotron_is_named():
    settings = _settings()
    result = explain("What is RUL?", CASE, PACK, settings, client=_client(settings, served_by="anthropic/claude-x"))
    assert "anthropic/claude-x via Nightshift" in result.label and "not Nemotron" in result.label


def test_sample_mode_answers_from_the_glossary_and_sends_nothing():
    settings, seen = _settings(sample_mode=True), []
    result = explain("What is a borescope?", CASE, PACK, settings, client=_client(settings, requests=seen))
    assert seen == [] and result.source == "glossary" and result.entries[0]["term"] == "Borescope"
    assert result.label.startswith("Sample mode: AI help is off and nothing leaves this machine.")


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_nightshift_down_or_slow_falls_back_to_the_glossary(error):
    seen = []
    result = explain("What does HPC mean?", CASE, PACK, _settings(), client=_failing(error, seen))
    assert len(seen) == 1 and result.source == "glossary"
    assert result.note == "Nightshift did not answer (LlmUnavailable). Matching glossary entries instead."
    assert result.entries[0]["term"] == "HPC"


def test_refusal_or_missing_token_falls_back_without_a_model_answer():
    settings = _settings()
    refused = LlmClient(settings, meterless_transport=mock_nightshift("other-token"))
    assert "LlmRefusal" in explain("What is RUL?", CASE, PACK, settings, client=refused).note
    unset, seen = _settings(meterless_runtime_token=""), []
    result = explain("What is RUL?", CASE, PACK, unset, client=_client(unset, requests=seen))
    assert seen == [] and "no Nightshift runtime token" in result.note


def test_empty_question_and_empty_answer():
    settings = _settings()
    assert explain("  ", CASE, PACK, settings).note == "Type a question first."
    blank = explain("What is RUL?", CASE, PACK, settings, client=_client(settings, reply="  "))
    assert blank.source == "glossary" and "empty answer" in blank.note


def test_prompt_without_a_brief_explains_terms_only():
    prompt = build_prompt("What is an AOG?", [], glossary(PACK))
    assert "- No brief is open; explain terms only." in prompt and "AOG (aircraft on ground)" in prompt
