"""POST /run and GET /health through a real ThreadingHTTPServer on a free port (sample mode, offline): JSON for every
answer, a capped and validated body, 405 for other methods, 503 when every run slot is busy, no exception text in a
500. Also the inputs that used to give a 500: historical engines, other scripts' digits, a mistyped critic reply, and
parallel first runs on a fresh case memory."""

import http.client
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest

import run
from agents.critic import _parse
from graph.runner import execute_run
from settings import Settings, get_settings
from tests.test_intake_llm import _settings as live_settings
from tools.llm import LlmClient, LlmResponse

HPC = "What should I do about FD001-A031?"


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CONTEXT_SQLITE_PATH", str(tmp_path / "context.sqlite"))
    get_settings.cache_clear()
    monkeypatch.setattr(run, "_slots", None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), run.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _request(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, (json.loads(data) if data else None)


def _raw(port: int, data: bytes) -> tuple[int, dict]:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(data)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body)


def test_health_and_run_answer_json_and_ignore_the_query_string(server):
    response, body = _request(server, "GET", "/health?probe=1")
    assert response.status == 200 and body["overall"] == "sample"
    assert response.getheader("Server") == "Cleris", "no Python version in the Server header"
    response, body = _request(server, "POST", "/run?x=1", json.dumps({"question": HPC}).encode(),
                              {"Content-Type": "application/json"})
    assert response.status == 200 and body["status"] == "needs_review"
    response, body = _request(server, "HEAD", "/health")
    assert response.status == 200 and body is None
    response, body = _request(server, "GET", "/nowhere")
    assert response.status == 404 and body["error"]["code"] == "not_found"


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_get_a_json_405(server, method):
    response, body = _request(server, method, "/run", b"{}")
    assert response.status == 405 and body["error"]["code"] == "method_not_allowed"
    assert response.getheader("Allow") == "GET, HEAD, POST"


def test_the_body_is_validated_and_capped(server):
    assert _raw(server, b"POST /run HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n")[1]["error"]["code"] == \
        "invalid_length"
    status, body = _raw(server, b"POST /run HTTP/1.1\r\nHost: x\r\nContent-Length: 1000000000000000\r\n\r\n")
    assert status == 413 and body["error"]["code"] == "too_large"
    status, body = _raw(server, b"POST /run HTTP/1.1\r\nHost: x\r\n\r\n")
    assert status == 411 and body["error"]["code"] == "length_required"
    bad_utf8 = b"\xff\xfe\xfa"
    status, body = _raw(server, b"POST /run HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\n\r\n" + bad_utf8)
    assert status == 400 and body["error"]["code"] == "invalid_json"
    deep = b"[" * 30_000 + b"]" * 30_000
    response, body = _request(server, "POST", "/run", deep)
    assert response.status == 400 and body["error"]["code"] == "invalid_json"
    status, body = _raw(server, b"BREW /run HTTP/1.1\r\nHost: x\r\n\r\n")
    assert status == 501 and body["error"]["code"] == "http_error", "protocol errors answer JSON too"
    response, body = _request(server, "POST", "/run", json.dumps({"question": "x" * 2001}).encode())
    assert response.status == 400 and body["error"]["message"].startswith("question:")


def test_a_failed_run_hides_its_internals_and_a_full_server_says_busy(server, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("UNIQUE constraint failed: memories.source at C:\\secret\\path")

    monkeypatch.setattr(run, "execute_run", broken)
    response, body = _request(server, "POST", "/run", json.dumps({"question": HPC}).encode())
    assert response.status == 500 and body["error"] == {"code": "run_failed",
                                                        "message": "the run failed; the server log has the details"}
    assert "secret" not in json.dumps(body) and body["run_id"]
    monkeypatch.setattr(run, "_slots", threading.BoundedSemaphore(1))
    run._slots.acquire()
    try:
        response, body = _request(server, "POST", "/run", json.dumps({"question": HPC}).encode())
        assert response.status == 503 and body["error"]["code"] == "busy" and response.getheader("Retry-After")
    finally:
        run._slots.release()


def test_recording_findings_is_refused_over_http_by_default(server):
    finding = {"asset_id": "FD001-A070", "kind": "borescope", "module": "hpc", "location": "HPC stage 3",
               "observation": "Tip erosion.", "inspector": "J. Inspector", "inspected_on": "2026-10-22", "cycle": 152,
               "measurement": {"parameter": "HPC-TIP", "value": 0.82, "unit": "mm"}}
    response, body = _request(server, "POST", "/run", json.dumps(
        {"question": "What should I do about FD001-A070?", "inspections": [finding], "record": True}).encode())
    assert response.status == 403 and body["error"]["code"] == "record_disabled"


@pytest.mark.parametrize("question", ["What should I do about FD001-H001?", "What should I do about FD٠٠١-A٠٣١?",
                                      "Status of ESN-٧٠٠١٣١?"])
def test_historical_engines_and_other_scripts_digits_ask_back(tmp_path, question):
    settings = Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "c.sqlite")
    case = execute_run(question, settings).case
    assert case.status == "needs_clarification", case.intent
    if "H001" in question:
        assert "historical engine" in case.intent.clarification


def test_fullwidth_ids_read_as_ascii(tmp_path):
    settings = Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "c.sqlite")
    case = execute_run("What should I do about ＦＤ００１-Ａ０３１?", settings).case
    assert case.status == "needs_review" and case.asset.asset_id == "FD001-A031"


@pytest.mark.parametrize("reply", [
    '{"outcome": "accept", "gaps": [], "challenges": 5}',
    '{"outcome": "accept", "gaps": 5, "challenges": [{"kind": ["wrong_engine"]}]}',
    '{"outcome": "accept", "gaps": [{"a": 1}], "challenges": [{"kind": {"x": 1}}, {"kind": "unsupported_number", '
    '"claim": "zero", "value": [1]}]}',
    '[1, 2, 3]',
])
def test_a_mistyped_critic_reply_never_fails_the_run(tmp_path, monkeypatch, reply):
    def chat(self, messages, **kwargs):
        return LlmResponse(text=reply, provider="meterless", requested_model="meterless/auto",
                           model="nvidia/nemotron-3-ultra-550b-a55b", duration_s=0.1)

    monkeypatch.setattr(LlmClient, "chat", chat)
    result = execute_run(HPC, live_settings(tmp_path)).result
    assert result["status"] == "needs_review" and result["verdicts"][-1]["outcome"] == "accept"


def test_the_critic_parser_coerces_types():
    parsed = _parse('{"outcome": "Accept", "gaps": "one gap", "challenges": [{"kind": "wrong_engine"}, 7, '
                    '{"kind": ["x"]}]}')
    assert parsed == {"outcome": "accept", "gaps": ["one gap"], "challenges": [{"kind": "wrong_engine"}]}


def test_parallel_first_runs_on_a_fresh_case_memory_all_finish(tmp_path):
    settings = Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "fresh" / "c.sqlite")
    questions = [HPC, "What is the status of FD001-A001?", "How long can FD003-A100 keep flying?",
                 "The two remaining-life estimates for FD001-A070 look different. What should I do?"] * 3
    with ThreadPoolExecutor(max_workers=len(questions)) as pool:
        statuses = list(pool.map(lambda q: execute_run(q, settings).result["status"], questions))
    assert statuses == ["needs_review"] * len(questions)
