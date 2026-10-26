"""The live-evidence summary (tools/live_evidence.py): the committed live traces and /health snapshot pass its checks;
a snapshot fallback, a non-Meterless context call, or a replayed or non-Nemotron critic reply fails them; and the
capture refuses to write a setting's value, a URL or an AWS host."""

import json

from settings import ROOT, Settings
from tools import live_evidence

TRACE = ROOT / "trace_examples" / "trace-live-hpc-FD001-A031.jsonl"


def test_committed_live_traces_and_health_snapshot_pass_the_checks(capsys):
    assert live_evidence.report() == 0
    out = capsys.readouterr().out
    assert out.count("Check      reads as a live run") == len(list(TRACE.parent.glob("trace-live-*.jsonl"))) >= 5
    assert "nemotron" in out.lower() and "all live services answered" in out
    for source in ("telemetry_pg (RDS Postgres)", "cmms_pg (RDS Postgres)", "knowledge_pg (RDS Postgres)",
                   "s3_files (S3)"):
        assert source in out


def _edited(tmp_path, edit) -> dict:
    lines = live_evidence.read_trace(TRACE)
    edit(lines)
    path = tmp_path / TRACE.name
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return live_evidence.summarise(path)


def _first(lines, **match) -> dict:
    return next(line for line in lines if all(line.get(k) == v for k, v in match.items()))


def test_a_fallback_or_a_replayed_critic_is_not_a_live_run(tmp_path):
    clean = _edited(tmp_path, lambda lines: None)
    assert clean["problems"] == [] and clean["engine"] == "FD001-A031" and clean["calls"]["H-MEM"] == 2

    def fallback(lines):
        line = _first(lines, action="zetaris_query")
        line["output_summary"] = line["output_summary"].replace("live Zetaris SQL", "snapshot cycles (timeout)")

    def sqlite(lines):
        _first(lines, action="context_call")["target_agent"] = "sqlite"

    def replayed(lines):
        _first(lines, agent_name="critic", action="disprove")["recorded"] = "recorded 25 Oct 2026"

    def other_model(lines):
        _first(lines, agent_name="critic", action="disprove")["model"] = "meta/llama-3.3-70b-instruct"

    assert _edited(tmp_path, fallback)["problems"] == ["Zetaris read not live: snapshot cycles (timeout)"]
    assert _edited(tmp_path, sqlite)["problems"] == ["context call on sqlite, not the Meterless service"]
    assert _edited(tmp_path, replayed)["problems"] == ["critic reply replayed from a recording, not a live call"]
    assert _edited(tmp_path, other_model)["problems"] == [
        "critic answered by meta/llama-3.3-70b-instruct, not a Nemotron model"]


def test_a_degraded_health_snapshot_fails():
    record = json.loads(live_evidence.HEALTH.read_text(encoding="utf-8"))
    assert live_evidence.health_problems(record) == []
    zetaris = next(c for c in record["health"]["components"] if c["key"] == "zetaris")
    zetaris.update(state="warn", summary="no answer")
    record["brief"]["degraded"] = ["zetaris"]
    assert live_evidence.health_problems(record) == ["zetaris is warn: no answer", "the brief ran degraded: zetaris"]


def test_capture_refuses_a_credential_host_or_url_and_needs_live_mode(capsys):
    settings = Settings(_env_file=None, zetaris_base_url="https://zetaris.example.test",
                        zetaris_api_key="zetaris-test-api-key", meterless_runtime_token="test-runtime-token")
    assert live_evidence.leaks('{"summary": "reachable · 1.2 s"}', settings) == []
    assert live_evidence.leaks('{"error": "zetaris-test-api-key"}', settings) == ["ZETARIS_API_KEY"]
    assert live_evidence.leaks('{"at": "https://zetaris.example.test/sql"}', settings) == ["ZETARIS_BASE_URL",
                                                                                          "a URL"]
    assert live_evidence.leaks('{"host": "db1.abc.eu-central-1.rds.amazonaws.com"}', settings) == [
        "an AWS host name"]
    assert live_evidence.capture_health(Settings(_env_file=None, sample_mode=True)) == 2
    assert "needs live mode" in capsys.readouterr().out
