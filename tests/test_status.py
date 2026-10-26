"""The live status strip and GET /health: component states from short, cached, read-only checks against mocked
services (no real network), nothing at all over the network in sample mode, and never a credential, token or host."""

import json
import threading
import time

import httpx
import pytest

import run
from settings import ROOT, Settings
from tools import llm_recordings, status, zetaris
from tools.mock_nightshift import mock_nightshift

TOKEN = "test-runtime-token"
API_KEY = "zetaris-test-api-key"
HOST = "zetaris.example.test"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


def _live(**overrides) -> Settings:
    values = dict(_env_file=None, sample_mode=False, zetaris_mode="live", zetaris_base_url=f"https://{HOST}",
                  zetaris_org_id="42", zetaris_api_key=API_KEY, meterless_runtime_token=TOKEN,
                  context_store="meterless", context_service_url="http://127.0.0.1:8710")
    values.update(overrides)
    return Settings(**values)


def _zetaris(slow: set = frozenset(), failing: set = frozenset(), seen: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        sql = json.loads(request.content)["sql"]
        if seen is not None:
            seen.append(sql)
        source = next(s for s in zetaris.SOURCES if f"{s}." in sql)
        if source in slow:
            raise httpx.ReadTimeout("stalled", request=request)
        if source in failing:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"data": [["x"]], "headers": ["id"], "timeUsed": 3, "total": 1})
    return httpx.MockTransport(handler)


def _context(body: dict | None = None) -> httpx.MockTransport:
    body = body or {"status": "ok", "world": {"relations": 412, "entities": {"document": 13}}, "hmem": {"memories": 200}}
    return httpx.MockTransport(lambda request: httpx.Response(200, json=body))


def _refused() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused by {request.url.host}", request=request)
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(status, "MODEL_SEEN", {})
    monkeypatch.setattr(zetaris, "LAST_OK", {})


def _no_secrets(payload) -> None:
    text = json.dumps(payload)
    for secret in (TOKEN, API_KEY, HOST, "127.0.0.1", "Bearer", "localhost"):
        assert secret not in text, secret


def test_sample_mode_shows_what_it_uses_and_checks_nothing(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("sample mode made a network call")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(llm_recordings, "RECORDINGS", ROOT / "data" / "snapshot" / "llm_recordings.json")
    board = status.StatusBoard()
    payload = board.get(Settings(_env_file=None, sample_mode=True))
    assert payload["overall"] == "sample"
    assert payload["summary"] == "Sample mode: snapshot · SQLite · recorded Nemotron"
    assert {c["key"]: c["state"] for c in payload["components"]} == {
        "mode": "off", "zetaris": "off", "context_service": "off", "nightshift": "off", "cmms": "off"}
    explicit = board.get(Settings(_env_file=None, sample_mode=True, context_store="meterless"), memory="meterless")
    assert explicit["summary"] == "Sample mode: snapshot · Meterless context service · recorded Nemotron"


def test_zetaris_check_reports_each_aws_source_through_zetaris():
    seen = []
    component = status.check_zetaris(_live(), transport=_zetaris(seen=seen))
    assert component.state == "ok" and component.summary.startswith("reachable · ")
    assert "last query" in component.summary and "never" not in component.summary
    assert [(p["name"], p["kind"], p["state"]) for p in component.parts] == [
        ("telemetry_pg", "RDS Postgres", "ok"), ("cmms_pg", "RDS Postgres", "ok"), ("knowledge_pg", "RDS Postgres", "ok"),
        ("s3_files", "S3", "ok")]
    assert len(seen) == 4 and all(sql.rstrip().upper().endswith("LIMIT 1") for sql in seen)
    slow = status.check_zetaris(_live(), transport=_zetaris(slow={"s3_files"}, failing={"knowledge_pg"}))
    assert slow.state == "warn" and slow.summary.startswith("2 of 4 sources answering")
    parts = {p["name"]: p["summary"] for p in slow.parts}
    assert parts["s3_files"] == "no answer in time" and parts["knowledge_pg"] == "HTTP 503"
    down = status.check_zetaris(_live(), transport=_refused())
    assert down.state == "warn" and down.chip.startswith("no answer")
    assert status.check_zetaris(_live(zetaris_api_key="")).summary == "not configured"
    assert status.check_zetaris(_live(zetaris_mode="snapshot")).state == "off"
    _no_secrets([c.as_dict() for c in (component, slow, down)])


def test_context_service_and_nightshift_checks():
    up = status.check_context_service(_live(), transport=_context())
    assert up.state == "ok" and up.summary.startswith("up · port 8710 · ") and "H-MEM 200 memories" in up.summary
    empty = status.check_context_service(_live(), transport=_context({"world": {}, "hmem": {}}))
    assert empty.state == "warn" and empty.chip.startswith("empty")
    down = status.check_context_service(_live(), transport=_refused())
    assert down.state == "warn" and down.summary == "unreachable · port 8710 · ConnectError · briefs use SQLite"
    assert status.check_context_service(_live(context_store="sqlite")).state == "off"

    online = status.check_nightshift(_live(), transport=mock_nightshift(TOKEN))
    assert online.state == "ok" and online.summary.startswith("online · critic model shown after the first live")
    status.note_model_reply("turbofan-maintenance-critic", MODEL)
    served = status.check_nightshift(_live(), transport=mock_nightshift(TOKEN))
    assert served.chip == "online · Nemotron 3 Ultra 550B A55B"
    status.note_model_reply("turbofan-maintenance-critic", "anthropic/claude-x")
    assert status.check_nightshift(_live(), transport=mock_nightshift(TOKEN)).state == "warn", "lane not Nemotron"
    refused = status.check_nightshift(_live(meterless_runtime_token="wrong"), transport=mock_nightshift(TOKEN))
    assert refused.state == "warn" and refused.summary.startswith("token refused")
    assert status.check_nightshift(_live(meterless_runtime_token="")).summary.startswith("no runtime token")
    _no_secrets([c.as_dict() for c in (up, empty, down, online, served, refused)])


def test_cmms_write_path_is_configuration_only():
    assert status.cmms_component(_live()).chip == "JSONL fallback"
    configured = status.cmms_component(_live(pg_app_user="app_rw", pg_app_password="secret-pw"))
    assert configured.state == "ok" and configured.chip == "configured"
    assert "secret-pw" not in json.dumps(configured.as_dict())


def test_board_caches_the_checks_and_never_blocks():
    calls, release = [], threading.Event()

    def checks(settings):
        calls.append(time.monotonic())
        release.wait(2)
        return [status.mode_component(settings), status.Component("zetaris", "Zetaris", "ok", "reachable · 0.4 s")]

    board = status.StatusBoard(checks)
    settings = _live(status_ttl_s=45)
    start = time.monotonic()
    first = board.get(settings)
    assert time.monotonic() - start < 0.5, "the strip never waits for a check"
    assert first["checked_at"] is None and any(c["summary"] == "checking…" for c in first["components"])
    release.set()
    done = board.get(settings, wait_s=2)
    assert done["overall"] == "ok" and done["checked_at"] and len(calls) == 1
    board.get(settings)
    board.get(settings, wait_s=1)
    assert len(calls) == 1, "a result younger than STATUS_TTL_S is reused"
    stale = _live(status_ttl_s=0)
    board.get(stale, wait_s=2)
    assert len(calls) == 2


def test_health_lists_the_component_states_without_secrets(monkeypatch):
    def checks(settings):
        return [status.mode_component(settings), status.check_zetaris(settings, transport=_zetaris()),
                status.check_context_service(settings, transport=_context()),
                status.check_nightshift(settings, transport=mock_nightshift(TOKEN)), status.cmms_component(settings)]

    monkeypatch.setattr(status, "BOARD", status.StatusBoard(checks))
    payload = run.health_payload(_live())
    assert payload["status"] == "ok" and payload["sample_mode"] is False and payload["overall"] == "degraded"
    states = {c["key"]: c["state"] for c in payload["components"]}
    assert states == {"mode": "ok", "zetaris": "ok", "context_service": "ok", "nightshift": "ok", "cmms": "warn"}
    _no_secrets(payload)
    sample = run.health_payload(Settings(_env_file=None, sample_mode=True))
    assert sample["overall"] == "sample" and sample["data"] == "snapshot"
