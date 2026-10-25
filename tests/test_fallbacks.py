"""End-to-end runs with one dependency down. Each run must still finish with a brief, and the brief must say
which fallback it used. Offline: no Zetaris, context-service, Nightshift or NVIDIA call leaves the machine."""

import httpx
import pytest

from casefile.context_store import SqliteStore
from casefile.load_context import load as load_context
from graph.runner import run_case
from settings import Settings
from tools import data_access
from tools.llm import LlmClient, LlmResponse
from tools.zetaris import ZetarisError

QUESTION = "What should I do about FD001-A031?"
CLOSED_PORT = "http://127.0.0.1:9"


def _settings(tmp_path, **overrides) -> Settings:
    values = dict(_env_file=None, sample_mode=False, zetaris_mode="snapshot", context_store="sqlite",
                  context_sqlite_path=tmp_path / "ctx.sqlite", meterless_base_url=f"{CLOSED_PORT}/v1",
                  meterless_runtime_token="test-token", critic_timeout_s=2.0)
    values.update(overrides)
    settings = Settings(**values)
    store = SqliteStore(settings.context_sqlite_path)
    load_context(store, settings.model_copy(update={"zetaris_mode": "snapshot"}))
    store.close()
    return settings


def _critic_reply(monkeypatch, text: str) -> None:
    def chat(self, messages, **kwargs):
        return LlmResponse(text=text, provider="meterless", requested_model="meterless/auto",
                           model="nvidia/nemotron-3-super-120b-a12b", duration_s=0.1)

    monkeypatch.setattr(LlmClient, "chat", chat)


class _DownZetaris:
    mode = "live"

    def __getattr__(self, name):
        def call(*args):
            raise ZetarisError("connection refused")
        return call


def _assert_brief(result):
    assert result["status"] == "needs_review"
    assert result["recommendation"] and result["recommendation"]["deadline"]
    assert any(e["rule"] == "rul_below_lead_time" for e in result["escalations"])


def test_zetaris_down_mid_run_uses_snapshot_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(data_access, "ZetarisSource", lambda settings: _DownZetaris())
    _critic_reply(monkeypatch, '{"outcome": "accept", "gaps": []}')
    result = run_case(QUESTION, _settings(tmp_path, zetaris_mode="live"))
    _assert_brief(result)
    assert result["mode"]["data"] == "snapshot"
    assert any(d.startswith("Zetaris unavailable (ZetarisError") for d in result["degraded"])
    queries = [e for e in result["evidence"].values() if e["kind"] == "query"]
    assert queries and all(e["source"].startswith("snapshot:") and e["degraded"] for e in queries)


def test_zetaris_not_configured_uses_snapshot_and_says_so(tmp_path, monkeypatch):
    _critic_reply(monkeypatch, '{"outcome": "accept", "gaps": []}')
    result = run_case(QUESTION, _settings(tmp_path, zetaris_mode="live", zetaris_base_url=""))
    _assert_brief(result)
    assert any(d.startswith("Zetaris not configured") for d in result["degraded"])


def test_context_service_down_uses_sqlite_case_memory(tmp_path, monkeypatch):
    _critic_reply(monkeypatch, '{"outcome": "accept", "gaps": []}')
    result = run_case(QUESTION, _settings(tmp_path, context_store="meterless", context_service_url=CLOSED_PORT))
    _assert_brief(result)
    assert result["mode"]["context_store"] == "sqlite"
    assert len(result["findings"]["similar_cases"]) == 3
    assert any("context-service unreachable" in d for d in result["degraded"])


def test_nightshift_down_keeps_deterministic_critic_and_flags_it(tmp_path):
    result = run_case(QUESTION, _settings(tmp_path))
    _assert_brief(result)
    assert any(d.startswith("LLM critic unavailable (LlmUnavailable)") for d in result["degraded"])
    assert result["verdicts"][-1]["outcome"] == "accept" and result["verdicts"][-1]["model"] is None


def test_critic_timeout_is_a_flagged_skip_not_a_failed_run(tmp_path, monkeypatch):
    def slow(self, request):
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", slow)
    result = run_case(QUESTION, _settings(tmp_path))
    _assert_brief(result)
    assert any(d.startswith("LLM critic unavailable") for d in result["degraded"])


def test_critic_prose_reply_is_skipped_not_trusted(tmp_path, monkeypatch):
    _critic_reply(monkeypatch, "The brief looks fine to me.")
    result = run_case(QUESTION, _settings(tmp_path))
    _assert_brief(result)
    assert any("LLM critic unavailable (ValueError)" in d for d in result["degraded"])


def test_llm_critic_is_skipped_when_the_run_is_almost_out_of_time(tmp_path, monkeypatch):
    """A slow Nightshift must not turn a finished brief into status=error at the 120 s cap."""
    called = []

    def boom(self, messages, **kwargs):
        called.append(True)
        raise AssertionError("LLM critic should not be called when remaining time is below its timeout")

    monkeypatch.setattr(LlmClient, "chat", boom)
    result = run_case(QUESTION, _settings(tmp_path, run_timeout_s=2.0, critic_timeout_s=45.0))
    _assert_brief(result)
    assert called == []
    assert any("remaining run time" in d for d in result["degraded"])


@pytest.mark.parametrize("outcome", ["revise", "reject"])
def test_llm_gaps_on_a_cited_brief_are_notes_not_a_veto(tmp_path, monkeypatch, outcome):
    _critic_reply(monkeypatch, f'{{"outcome": "{outcome}", "gaps": ["slot date not cited"]}}')
    result = run_case(QUESTION, _settings(tmp_path))
    _assert_brief(result)
    last = result["verdicts"][-1]
    assert last["outcome"] == "accept" and "slot date not cited" in last["gaps"]
    assert last["model"] == "nvidia/nemotron-3-super-120b-a12b"
