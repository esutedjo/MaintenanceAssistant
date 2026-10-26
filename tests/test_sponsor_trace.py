"""Sponsor technology in the trace: one line per Zetaris statement (purpose, tables, rows, latency, live or snapshot)
and one per World Model or H-MEM call (operation, backend, latency), never a credential, host or connection string.
Sample mode honours an explicit CONTEXT_STORE=meterless. Offline: no service is called."""

import json
from datetime import datetime, timezone

import httpx
import pandas as pd
import pytest

from agents.runtime import _query_call
from casefile import calls
from casefile import context_store as context_module
from casefile.calls import CALL_ACTIONS, TracedStore
from casefile.context_store import MeterlessStore, SqliteStore, open_context_store
from casefile.decision_log import derive_log
from graph.runner import execute_run
from settings import ROOT, Settings
from tools.data_access import QueryResult

HPC = "What should I do about FD001-A031?"
CLOSED_PORT = "http://127.0.0.1:9"
NOT_SHOWN = ("Bearer", "password", "api_key", "http://", "https://", "127.0.0.1", "jdbc:", "SELECT")


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def _lines(result: dict) -> list[dict]:
    return [json.loads(line) for line in (ROOT / result["trace_file"]).read_text(encoding="utf-8").splitlines()]


def test_each_read_and_each_context_call_gets_one_trace_line(settings):
    outcome = execute_run(HPC, settings)
    lines = _lines(outcome.result)
    queries = [line for line in lines if line["action"] == "zetaris_query"]
    context = [line for line in lines if line["action"] == "context_call"]
    cycles = next(q for q in queries if q["output_summary"].startswith("telemetry cycles for FD001-A031"))
    assert (cycles["agent_name"], cycles["target_agent"], cycles["status"]) == ("data", "zetaris", "success")
    assert " · telemetry_pg.cycles · 196 rows · " in cycles["output_summary"]
    assert cycles["output_summary"].endswith(" s · snapshot cycles.parquet, no Zetaris call")
    assert cycles["duration_s"] is not None
    purposes = {q["output_summary"].split(" · ")[0] for q in queries}
    assert {"asset registry for FD001-A031", "free hangar slots", "document sections for turbofan_cmapss",
            "parts stock and lead time for 5 part number(s)"} <= purposes
    assert len(queries) == len({q["input_summary"] for q in queries}), "the run's cache: one line per query"
    search = next(c for c in context if c["output_summary"].startswith("H-MEM search tags case"))
    assert search["agent_name"] == "diagnostics" and " · sqlite · 3 hits · " in search["output_summary"]
    assert any(c["output_summary"].startswith("World Model query failure_mode:turbofan_cmapss:hpc_degradation")
               for c in context)
    assert all(c["target_agent"] == "sqlite" for c in context)
    for line in [*queries, *context]:
        assert not any(word in json.dumps(line) for word in NOT_SHOWN), line
    order = [(line["agent_name"], line["action"]) for line in lines]
    assert order.index(("data", "zetaris_query")) < order.index(("data", "query_sources")), "calls come first"
    case = outcome.case
    assert any(e.action in CALL_ACTIONS for e in case.trace), "the case file's trace (Trace tab) has them too"
    assert [e for e in derive_log(case) if e.decision == "proposed plan"], "the decision log skips call lines"
    assert len([e for e in derive_log(case) if e.actor == "maint_planner"]) == 1


def test_a_live_statement_says_live_and_a_fallback_says_why():
    frame = pd.DataFrame({"asset_id": ["FD001-A031"] * 2})
    at = datetime.now(timezone.utc)
    live = _query_call("cycles", ("FD001-A031",), QueryResult(frame, "zetaris:telemetry_pg.cycles", "SELECT …", 1.2),
                       1.25, at)
    assert live.output_summary == "telemetry cycles for FD001-A031 · telemetry_pg.cycles · 2 rows · 1.25 s · live Zetaris SQL"
    assert live.status == "success" and live.target == "zetaris"
    down = QueryResult(frame, "snapshot:cycles.parquet",
                       degraded="Zetaris unavailable (ConnectError: host unreachable); using snapshot")
    line = _query_call("cycles", ("FD001-A031",), down, 0.02, at)
    assert line.status == "fallback" and line.output_summary.endswith("snapshot cycles.parquet (Zetaris unavailable)")
    assert "host" not in line.output_summary


def test_context_calls_record_not_found_and_failures(tmp_path):
    store = TracedStore(SqliteStore(tmp_path / "c.sqlite"))
    with calls.collecting() as made:
        with pytest.raises(KeyError):
            store.query_entity("asset:turbofan_cmapss:FD001-A999")
        store.search_memory("HPC removal", top_n=3, tags=["case"])
    assert [c.output_summary.split(" · ")[2] for c in made] == ["not found", "0 hits"]
    assert all(c.action == "context_call" and c.target == "sqlite" for c in made)
    store.close()
    with calls.collecting() as outside:
        pass
    assert outside == []


def test_sample_mode_uses_sqlite_unless_context_store_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTEXT_STORE")                       # conftest pins sqlite; this test sets it itself
    base = dict(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "c.sqlite",
                context_service_url=CLOSED_PORT)
    default = open_context_store(Settings(**base))
    assert default.backend == "sqlite" and default.degraded_reason is None, "judges need no Node"
    default.close()
    monkeypatch.setenv("CONTEXT_STORE", "meterless")
    down = open_context_store(Settings(**base))
    assert down.backend == "sqlite" and down.degraded_reason.startswith("context-service unreachable")
    down.close()

    class Up(MeterlessStore):
        def __init__(self, base_url, timeout_s=10.0, transport=None):
            super().__init__(base_url, timeout_s, httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": 1})))

    monkeypatch.setattr(context_module, "MeterlessStore", Up)
    up = open_context_store(Settings(**base))
    assert up.backend == "meterless" and up.degraded_reason is None
    up.close()


def test_explicit_meterless_in_sample_mode_falls_back_to_sqlite_and_flags_it(tmp_path):
    result = execute_run(HPC, Settings(_env_file=None, sample_mode=True, context_store="meterless",
                                       context_service_url=CLOSED_PORT,
                                       context_sqlite_path=tmp_path / "c.sqlite")).result
    assert result["status"] == "needs_review" and result["mode"]["context_store"] == "sqlite"
    assert any("context-service unreachable" in d for d in result["degraded"])
    assert len(result["findings"]["similar_cases"]) == 3
