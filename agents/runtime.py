"""What every node needs besides the case file: settings, asset pack, data source, context store and trace writer.
Passed to nodes through the LangGraph config (configurable["ctx"]); never stored in the checkpointed state."""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig

from casefile.context_store import ContextStore
from casefile.schemas import CaseFile, TraceEvent, content_hash
from casefile.trace import TraceWriter
from settings import Settings
from tools.data_access import DataSource, QueryResult

log = logging.getLogger("agents")


class CachedSource:
    """Memoises DataSource calls for one run: parallel agents share one query per (method, arguments)."""

    def __init__(self, source: DataSource):
        self.source = source
        self._cache: dict[tuple, QueryResult] = {}
        self._locks: dict[tuple, threading.Lock] = {}
        self._guard = threading.Lock()

    @property
    def mode(self) -> str:
        return self.source.mode

    @property
    def degraded(self) -> str | None:
        return getattr(self.source, "degraded", None)

    def __getattr__(self, name: str):
        method = getattr(self.source, name)
        if not callable(method):
            return method

        def call(*args):
            key = (name, *(tuple(a) if isinstance(a, list) else a for a in args))
            with self._guard:
                lock = self._locks.setdefault(key, threading.Lock())
            with lock:
                if key not in self._cache:
                    self._cache[key] = method(*args)
                return self._cache[key]
        return call


@dataclass
class RunContext:
    run_id: str
    settings: Settings
    pack: dict
    data: CachedSource
    store: ContextStore
    trace: TraceWriter
    started: float = field(default_factory=time.monotonic)


def context(config: RunnableConfig) -> RunContext:
    return config["configurable"]["ctx"]


@dataclass
class NodeResult:
    """What an agent function returns: the state update plus what the trace line should say."""
    update: dict[str, Any]
    summary: str
    status: str = "success"
    target_agent: str | None = None
    model: str | None = None
    confidence: float | None = None
    retry_count: int = 0


def node(agent: str, action: str, describe_input: Callable[[CaseFile], str]):
    """Wrap an agent function (state, ctx) -> NodeResult as a graph node that times it, writes the trace line
    and turns an exception into an error trace line plus status "error"."""

    def wrap(fn: Callable[[CaseFile, RunContext], NodeResult]):
        def run(state: CaseFile, config: RunnableConfig) -> dict[str, Any]:
            ctx = context(config)
            input_summary = describe_input(state)
            start = time.monotonic()
            try:
                if time.monotonic() - ctx.started > ctx.settings.run_timeout_s:
                    raise TimeoutError(f"run exceeded {ctx.settings.run_timeout_s:.0f}s")
                result = fn(state, ctx)
            except Exception as error:  # the trace must record every failed step; the run ends as "error"
                log.exception("%s failed", agent)
                result = NodeResult({"status": "error"}, f"{type(error).__name__}: {error}"[:300], status="error")
            update = result.update
            evidence = update.get("evidence", {})
            event = TraceEvent(
                run_id=ctx.run_id, agent_name=agent, action=action, input_summary=input_summary,
                output_summary=result.summary, target_agent=result.target_agent, model=result.model,
                confidence=result.confidence, retry_count=result.retry_count, status=result.status,
                duration_s=round(time.monotonic() - start, 3), input_hash=content_hash(input_summary),
                evidence_ids=sorted(evidence),
            )
            ctx.trace.write(event)
            degraded = sorted({e.degraded for e in evidence.values() if e.degraded} - set(state.degraded))
            return {**update, "trace": [event], **({"degraded": degraded} if degraded else {})}
        # LangGraph passes `config` only if it sees it in the signature, so no functools.wraps here.
        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        return run
    return wrap
