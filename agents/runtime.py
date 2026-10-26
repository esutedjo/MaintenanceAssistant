"""What every node needs besides the case file: settings, asset pack, data source, context store and trace writer.
Passed to nodes through the LangGraph config (configurable["ctx"]); never stored in the checkpointed state."""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig

from casefile import calls as service_calls
from casefile.context_store import ContextStore
from casefile.schemas import CaseFile, EvidenceLedger, Findings, Task, TraceEvent, content_hash, merge_findings, utcnow
from casefile.trace import TraceWriter
from settings import Settings
from tools.data_access import QUERY_PURPOSE, DataSource, QueryResult

log = logging.getLogger("agents")


def _query_call(name: str, args: tuple, result: QueryResult, took: float, at) -> service_calls.Call:
    """One trace line for a structured-data read: purpose, Zetaris tables, rows, latency, live or snapshot."""
    purpose, tables = QUERY_PURPOSE.get(name, (name.replace("_", " "), "?"))
    subject = ""
    if args:
        first = args[0]
        subject = f" for {len(first)} part number(s)" if isinstance(first, (list, tuple)) else f" for {first}"
    if result.source.startswith("zetaris:"):
        mode, status = "live Zetaris SQL", "success"
    elif result.degraded:
        mode, status = f"snapshot {result.source.split(':', 1)[-1]} ({result.degraded.split(' (', 1)[0]})", "fallback"
    else:
        mode, status = f"snapshot {result.source.split(':', 1)[-1]}, no Zetaris call", "success"
    rows = len(result.frame)
    return service_calls.Call(
        service_calls.ZETARIS_QUERY, "zetaris", f"{name}{subject}",
        f"{purpose}{subject} · {tables} · {rows} row{'s' if rows != 1 else ''} · {took:.2f} s · {mode}",
        took, status, at)


class CachedSource:
    """Memoises DataSource calls for one run: parallel agents share one query per (method, arguments). Each query
    that reaches the source (a cache miss or a fresh re-read) adds one trace line to the agent step that made it."""

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

    def _query(self, name: str, *args) -> QueryResult:
        start, at = time.monotonic(), utcnow()
        result = getattr(self.source, name)(*args)
        if isinstance(result, QueryResult):
            service_calls.note(_query_call(name, args, result, time.monotonic() - start, at))
        return result

    def fresh(self, name: str, *args) -> QueryResult:
        """A re-query that skips the run's cache (a repair or follow-up must see the source again)."""
        return self._query(name, *args)

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
                    self._cache[key] = self._query(name, *args)
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
    # The recording's label ("recorded Nemotron response (captured <date>)") when the model reply was a sample-mode
    # replay: the trace line then says recorded=true, so it never reads as a live call.
    recorded: str | None = None
    confidence: float | None = None
    retry_count: int = 0
    action: str | None = None                   # overrides the node's default action on the trace line
    # One trace line each: (action, target agent, reason). How the planner delegates follow-ups and routes gaps.
    handoffs: list[tuple[str, str, str]] = field(default_factory=list)


TaskHandler = Callable[[CaseFile, RunContext, Task, EvidenceLedger], tuple[str, Findings | None]]


def run_tasks(state: CaseFile, ctx: RunContext, agent: str, handlers: dict[str, TaskHandler]) -> NodeResult:
    """Run the tasks the planner delegated to this agent (follow-ups by kind; repairs, "T-fix-*", by "repair").
    A task that raises is marked failed and noted; the run goes on without it."""
    ledger = EvidenceLedger(agent, state.evidence)
    done, lines, degraded, findings = [], [], [], Findings()
    repair = False
    for task in state.ready(agent):
        is_repair = task.id.startswith("T-fix-")
        repair |= is_repair
        handler = handlers.get("repair" if is_repair else task.kind or "")
        try:
            if handler is None:
                raise ValueError(f"{agent} has no handler for {task.kind}")
            line, found = handler(state, ctx, task, ledger)
            done.append(task.model_copy(update={"status": "done"}))
            if found is not None:
                findings = merge_findings(findings, found)
        except Exception as error:  # a follow-up or repair adds evidence; losing one must not lose the brief
            log.exception("%s task %s failed", agent, task.id)
            line = f"{task.kind} failed ({type(error).__name__}: {str(error)[:160]})"
            done.append(task.model_copy(update={"status": "failed"}))
            degraded.append(f"{agent} {task.kind} did not complete ({type(error).__name__}); brief built without it")
        lines.append(line)
    update: dict[str, Any] = {"plan": done, "evidence": ledger.records}
    if findings != Findings():
        update["findings"] = findings
    if degraded:
        update["degraded"] = degraded
    return NodeResult(update, "; ".join(lines) or "no task", status="fallback" if degraded else "success",
                      action="repair" if repair else "follow_up", target_agent="maint_planner" if repair else None)


def node(agent: str, action: str, describe_input: Callable[[CaseFile], str]):
    """Wrap an agent function (state, ctx) -> NodeResult as a graph node that times it, writes the trace line
    and turns an exception into an error trace line plus status "error". The Zetaris queries and context-store calls
    the agent made come first, one line each (casefile/calls.py)."""

    def wrap(fn: Callable[[CaseFile, RunContext], NodeResult]):
        def run(state: CaseFile, config: RunnableConfig) -> dict[str, Any]:
            ctx = context(config)
            input_summary = describe_input(state)
            start = time.monotonic()
            with service_calls.collecting() as made:
                try:
                    overtime = time.monotonic() - ctx.started > ctx.settings.run_timeout_s
                    # Specialists that have not produced a brief yet must stop. Once we have findings, finish
                    # the case (critic skips the LLM; rule checks, escalation and close are milliseconds).
                    if overtime and agent not in {"critic", "rule_checks", "escalation", "close", "maint_planner"}:
                        raise TimeoutError(f"run exceeded {ctx.settings.run_timeout_s:.0f}s")
                    result = fn(state, ctx)
                except Exception as error:  # the trace must record every failed step; the run ends as "error"
                    log.exception("%s failed", agent)
                    result = NodeResult({"status": "error"}, f"{type(error).__name__}: {error}"[:300], status="error")
            update = result.update
            evidence = update.get("evidence", {})
            event = TraceEvent(
                run_id=ctx.run_id, agent_name=agent, action=result.action or action, input_summary=input_summary,
                output_summary=result.summary, target_agent=result.target_agent, model=result.model,
                recorded=(result.recorded is not None) if result.model else None,
                confidence=result.confidence, retry_count=result.retry_count, status=result.status,
                duration_s=round(time.monotonic() - start, 3), input_hash=content_hash(input_summary),
                evidence_ids=sorted(evidence),
            )
            events = [*service_calls.events(ctx.run_id, agent, made), event,
                      *(TraceEvent(run_id=ctx.run_id, agent_name=agent, action=kind, input_summary=input_summary,
                                   output_summary=reason, target_agent=target, input_hash=event.input_hash,
                                   duration_s=0.0) for kind, target, reason in result.handoffs)]
            for item in events:
                ctx.trace.write(item)
            degraded = sorted(({e.degraded for e in evidence.values() if e.degraded} | set(update.get("degraded", [])))
                              - set(state.degraded))
            return {**update, "trace": events, **({"degraded": degraded} if degraded else {})}
        # LangGraph passes `config` only if it sees it in the signature, so no functools.wraps here.
        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        return run
    return wrap
