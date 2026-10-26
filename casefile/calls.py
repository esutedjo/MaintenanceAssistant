"""Sponsor-technology calls made inside an agent step: each Zetaris SQL statement (or its snapshot stand-in) and each
context-store call (World Model or H-MEM, Meterless or SQLite). The node wrapper collects them while the agent runs
and writes one trace line per call before the agent's own line (agents/runtime.py `node`). Lines carry the purpose,
tables or operation, rows or hits, latency and backend; never credentials, hosts or connection strings.

Collection is per agent step (a context variable), so parallel agents never mix their lines; a call outside a step
(seeding the sample case memory, the UI's checks) is not traced."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from casefile.schemas import TraceEvent, utcnow

ZETARIS_QUERY = "zetaris_query"
CONTEXT_CALL = "context_call"
# Trace actions that record a call, not a decision: the decision log and step counts skip them.
CALL_ACTIONS = {ZETARIS_QUERY, CONTEXT_CALL}


@dataclass
class Call:
    action: str
    target: str               # "zetaris" (live or snapshot stand-in) or the context-store backend
    input_summary: str
    output_summary: str
    duration_s: float
    status: str = "success"
    at: datetime = field(default_factory=utcnow)


_calls: ContextVar[list[Call] | None] = ContextVar("sponsor_calls", default=None)


def note(call: Call) -> None:
    calls = _calls.get()
    if calls is not None:
        calls.append(call)


@contextmanager
def collecting() -> Iterator[list[Call]]:
    calls: list[Call] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def events(run_id: str, agent: str, calls: list[Call]) -> list[TraceEvent]:
    return [TraceEvent(timestamp=c.at, run_id=run_id, agent_name=agent, action=c.action, input_summary=c.input_summary,
                       output_summary=c.output_summary, target_agent=c.target, status=c.status,
                       duration_s=round(c.duration_s, 3)) for c in calls]


def _counts(result: Any) -> str:
    """{"entities": {"created": 1, "unchanged": 3}, ...} -> "entities 1 created, 3 unchanged; ..."."""
    parts = [f"{name} " + ", ".join(f"{n} {what}" for what, n in counts.items())
             for name, counts in (result or {}).items() if isinstance(counts, dict)]
    return "; ".join(parts)[:200] or "written"


def _short(values: Any, limit: int = 3) -> str:
    items = [str(v) for v in (values if isinstance(values, (list, tuple, set)) else [values])]
    return ", ".join(items[:limit]) + (f" +{len(items) - limit}" if len(items) > limit else "")


class TracedStore:
    """Wraps a ContextStore: each World Model or H-MEM operation inside an agent step becomes one trace line."""

    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def _timed(self, engine: str, operation: str, detail: str, fn, describe) -> Any:
        start, at = time.monotonic(), utcnow()
        backend = self._store.backend
        what = f"{engine} {operation} {detail}"
        try:
            result = fn()
        except KeyError:
            took = time.monotonic() - start
            note(Call(CONTEXT_CALL, backend, what, f"{what} · {backend} · not found · {took:.3f} s", took, at=at))
            raise
        except Exception as error:
            took = time.monotonic() - start
            note(Call(CONTEXT_CALL, backend, what, f"{what} · {backend} · failed ({type(error).__name__}) · "
                      f"{took:.3f} s", took, status="fallback", at=at))
            raise
        took = time.monotonic() - start
        note(Call(CONTEXT_CALL, backend, what, f"{what} · {backend} · {describe(result)} · {took:.3f} s", took, at=at))
        return result

    def query_entity(self, id_: str):
        return self._timed("World Model", "query", id_, lambda: self._store.query_entity(id_),
                           lambda r: f"{len(r.get('outgoing') or [])} outgoing, {len(r.get('incoming') or [])} "
                                     "incoming edges")

    def list_entities(self, type_: str, where: dict | None = None):
        return self._timed("World Model", "list", type_, lambda: self._store.list_entities(type_, where),
                           lambda r: f"{len(r)} entities")

    def write_world(self, batch):
        return self._timed("World Model", "write", f"{len(batch.entities)} entities, {len(batch.facts)} facts",
                           lambda: self._store.write_world(batch), _counts)

    def write_memories(self, memories):
        return self._timed("H-MEM", "write", f"{len(memories)} memories", lambda: self._store.write_memories(memories),
                           lambda r: f"{r.get('created', 0)} created, {r.get('unchanged', 0)} unchanged")

    def search_memory(self, query: str, top_n: int = 5, tags: list[str] | None = None, threshold: float | None = None):
        return self._timed("H-MEM", "search", f"tags {_short(tags or [], 4)} top {top_n}",
                           lambda: self._store.search_memory(query, top_n=top_n, tags=tags, threshold=threshold),
                           lambda r: f"{len(r)} hits")

    def feedback(self, memory_id: str, kind: str):
        return self._timed("H-MEM", "feedback", f"{memory_id} {kind}", lambda: self._store.feedback(memory_id, kind),
                           lambda r: f"trust now {float(r):.2f}")

    def close(self) -> None:
        self._store.close()
