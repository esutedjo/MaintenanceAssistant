"""Run one question through the graph and return the JSON-ready result."""

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agents.runtime import CachedSource, RunContext
from casefile.calls import TracedStore
from casefile.context_store import open_context_store
from casefile.decision_log import derive_log
from casefile.load_context import load as load_context
from casefile.schemas import CaseFile, InspectionFinding
from casefile.trace import TraceWriter
from data.db import load_pack
from graph.build import build_graph
from settings import ROOT, Settings, get_settings
from tools.data_access import DataSource, open_data_source

MAX_GRAPH_STEPS = 25
# Who takes part, for the API result: (name, role, uses a model, can block the brief or send it back).
AGENTS = [
    ("intake", "Reads the question and resolves the engine, or asks back", True, True),
    ("planner", "Assigns round 1, delegates follow-ups the findings call for, routes each gap to the agent that "
                "can fix it", False, False),
    ("data", "Telemetry, input plausibility, history, schedule, slots, entered inspection findings; inspection "
             "history and targeted re-queries", False, False),
    ("prognostics", "RUL methods A and B, SHAP drivers, anomaly flag; back-test at the last inspection", False, False),
    ("diagnostics", "Fault-mode match and similar past cases; checks both candidates when unclear; checks an "
                    "inspection finding against the fault mode", False, False),
    ("knowledge", "Applicable documents in precedence order, mandates decided in code; the limit section for each "
                  "inspection finding, compared in code; binds a missing mandate", False, False),
    ("maint_planner", "Task, parts, slot and deadline from the RUL lower bound; applies the routed fixes",
     False, False),
    ("rule_checks", "Deterministic gate: citations, numbers, parts, slots, deadlines, mandates", False, True),
    ("critic", "Deterministic checks, then Nemotron challenges that code confirms or refutes", True, True),
    ("escalation", "Deterministic escalation rules from the asset pack", False, False),
]


@dataclass
class RunOutcome:
    """Full case file for the UI, plus the stripped JSON `run.py` and examples use."""

    case: CaseFile
    result: dict
    duration_s: float


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


_SEEDED: set[tuple] = set()
_SEED_LOCK = threading.Lock()


def _seed_once(store, settings: Settings, new_file: bool) -> None:
    """Sample mode seeds the case memory from the snapshot once per process and store (idempotent anyway), under a
    lock, so parallel first runs on a fresh store never race; a SQLite file that was missing is seeded again."""
    key = (store.backend, str(settings.context_sqlite_path) if store.backend == "sqlite"
           else settings.context_service_url)
    with _SEED_LOCK:
        if key in _SEEDED and not (new_file and store.backend == "sqlite"):
            return
        load_context(store, settings)
        _SEEDED.add(key)


def open_context(settings: Settings, run_id: str, data: DataSource | None = None) -> RunContext:
    pack = load_pack()
    new_file = not Path(settings.context_sqlite_path).exists()
    store = open_context_store(settings)
    if settings.sample_mode:
        _seed_once(store, settings, new_file)
    # Each World Model / H-MEM call and each Zetaris query an agent makes gets one trace line (casefile/calls.py).
    return RunContext(run_id=run_id, settings=settings, pack=pack,
                      data=CachedSource(data or open_data_source(settings)), store=TracedStore(store),
                      trace=TraceWriter(run_id))


def model_calls(case: CaseFile) -> list[dict]:
    """Which agent read a model reply in this run, and whether it was a live call or a sample-mode replay of a
    recorded reply (`recorded`: true, `source` names the capture date)."""
    calls = []
    if case.intent and case.intent.resolved_by:
        calls.append({"agent": "intake", "model": case.intent.resolved_by, "recorded": bool(case.intent.recorded),
                      "source": case.intent.recorded or "live Nightshift call"})
    calls += [{"agent": "critic", "round": v.round, "model": v.model, "recorded": bool(v.recorded),
               "source": v.recorded or "live Nightshift call"}
              for v in case.verdicts if v.gate == "critic" and v.model]
    return calls


def result_json(case: CaseFile, ctx: RunContext, duration_s: float) -> dict:
    return {
        "run_id": ctx.run_id,
        "trace_id": ctx.trace.path.stem,
        "status": case.status,
        "question": case.question,
        "mode": {"sample_mode": ctx.settings.sample_mode, "data": ctx.data.mode, "context_store": ctx.store.backend},
        "degraded": sorted(set(case.degraded)),
        "asset": case.asset.model_dump() if case.asset else None,
        "intent": case.intent.model_dump() if case.intent else None,
        "findings": case.findings.model_dump(mode="json"),
        "recommendation": case.recommendation.model_dump(mode="json") if case.recommendation else None,
        "escalations": [e.model_dump(mode="json") for e in case.escalations],
        "verdicts": [v.model_dump(mode="json") for v in case.verdicts],
        "evidence": {k: {"kind": e.kind, "source": e.source, "summary": e.summary, "produced_by": e.produced_by,
                         "degraded": e.degraded} for k, e in sorted(case.evidence.items())},
        # Who decided what, derived from the case file; the engineer's sign-off is appended at decision time.
        "decision_log": [e.model_dump(mode="json", exclude_none=True) for e in derive_log(case)],
        "agents": [{"name": n, "role": r, "uses_model": m, "can_block_or_send_back": b} for n, r, m, b in AGENTS],
        "model_calls": model_calls(case),
        "trace_file": str(ctx.trace.path.relative_to(ROOT)),
        "duration_s": round(duration_s, 2),
        "execution_time_seconds": round(duration_s, 3),
    }


def execute_run(question: str, settings: Settings | None = None, run_id: str | None = None,
                data: DataSource | None = None, inspections: list[InspectionFinding] | None = None) -> RunOutcome:
    """`data` replaces the configured source (tests and the eval feed corrupted inputs through it). `inspections` are
    findings entered with the question; the data agent adds the engine's stored findings to them."""
    settings = settings or get_settings()
    ctx = open_context(settings, run_id or new_run_id(), data)
    start = time.monotonic()
    try:
        graph = build_graph()
        config = {"configurable": {"thread_id": ctx.run_id, "ctx": ctx}, "recursion_limit": MAX_GRAPH_STEPS}
        state = graph.invoke(CaseFile(case_id=ctx.run_id, question=question, inspections=inspections or []), config)
        case = CaseFile.model_validate(state)
        duration = time.monotonic() - start
        return RunOutcome(case, result_json(case, ctx, duration), duration)
    finally:
        ctx.store.close()


def run_case(question: str, settings: Settings | None = None, run_id: str | None = None,
             inspections: list[InspectionFinding] | None = None) -> dict:
    return execute_run(question, settings, run_id, inspections=inspections).result
