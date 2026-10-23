"""Run one question through the graph and return the JSON-ready result."""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from agents.runtime import CachedSource, RunContext
from casefile.context_store import open_context_store
from casefile.load_context import load as load_context
from casefile.schemas import CaseFile
from casefile.trace import TraceWriter
from data.db import load_pack
from graph.build import build_graph
from settings import ROOT, Settings, get_settings
from tools.data_access import DataSource, open_data_source

MAX_GRAPH_STEPS = 25


@dataclass
class RunOutcome:
    """Full case file for the UI, plus the stripped JSON `run.py` and examples use."""

    case: CaseFile
    result: dict
    duration_s: float


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


def open_context(settings: Settings, run_id: str, data: DataSource | None = None) -> RunContext:
    pack = load_pack()
    store = open_context_store(settings)
    if settings.sample_mode:
        load_context(store, settings)       # idempotent: seeds the local case memory from the snapshot
    return RunContext(run_id=run_id, settings=settings, pack=pack,
                      data=CachedSource(data or open_data_source(settings)), store=store, trace=TraceWriter(run_id))


def result_json(case: CaseFile, ctx: RunContext, duration_s: float) -> dict:
    return {
        "run_id": ctx.run_id,
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
        "trace_file": str(ctx.trace.path.relative_to(ROOT)),
        "duration_s": round(duration_s, 2),
    }


def execute_run(question: str, settings: Settings | None = None, run_id: str | None = None,
                data: DataSource | None = None) -> RunOutcome:
    """`data` replaces the configured source (tests and the eval feed corrupted inputs through it)."""
    settings = settings or get_settings()
    ctx = open_context(settings, run_id or new_run_id(), data)
    start = time.monotonic()
    try:
        graph = build_graph()
        config = {"configurable": {"thread_id": ctx.run_id, "ctx": ctx}, "recursion_limit": MAX_GRAPH_STEPS}
        state = graph.invoke(CaseFile(case_id=ctx.run_id, question=question), config)
        case = CaseFile.model_validate(state)
        duration = time.monotonic() - start
        return RunOutcome(case, result_json(case, ctx, duration), duration)
    finally:
        ctx.store.close()


def run_case(question: str, settings: Settings | None = None, run_id: str | None = None) -> dict:
    return execute_run(question, settings, run_id).result
