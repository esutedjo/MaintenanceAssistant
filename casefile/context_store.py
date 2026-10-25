"""Shared context for the agents: World Model (asset structure, case events) and H-MEM (past cases).

MeterlessStore talks to context-service (vendored Meterless engines). SqliteStore is the fallback with the
same write semantics: provenance required, idempotent writes, single-valued edges, H-MEM feedback deltas.
"""

import json
import re
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from settings import Settings

FeedbackKind = Literal["helpful", "not_helpful", "wrong"]
FEEDBACK_DELTAS: dict[str, float] = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20}


class Source(BaseModel):
    kind: str
    by: str | None = None
    url: str | None = None
    runId: str | None = None
    at: str | None = None


class EntityIn(BaseModel):
    type: str
    key: str
    name: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class RelationIn(BaseModel):
    from_: str = Field(alias="from")
    type: str
    to: str
    context: str | None = None
    attrs: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class FactIn(BaseModel):
    about: str
    predicate: str
    value: Any
    confidence: float | None = None


class WorldBatch(BaseModel):
    system: str
    source: Source
    entities: list[EntityIn] = Field(default_factory=list)
    relations: list[RelationIn] = Field(default_factory=list)
    facts: list[FactIn] = Field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class MemoryProvenance(BaseModel):
    origin: Literal["document", "plan_completion", "user_correction", "model_response", "manual"]
    label: str
    refs: list[str] = Field(default_factory=list)


class MemoryIn(BaseModel):
    content: str
    source: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    provenance: MemoryProvenance | None = None
    goalRunId: str | None = None


class MemoryHit(BaseModel):
    id: str
    content: str
    source: str
    tags: list[str]
    confidence: float
    relevance: float


def entity_id(type_: str, system: str, key: str) -> str:
    """Meterless World Model grammar for externally keyed entities."""
    return f"{type_}:{system}:{key}"


def check_single_valued(relations: list[RelationIn]) -> None:
    """World Model edges are single-valued per (from, type, context): a second target closes the first."""
    seen: dict[tuple, str] = {}
    for r in relations:
        slot = (r.from_, r.type, r.context or "")
        if slot in seen and seen[slot] != r.to:
            raise ValueError(f"{r.from_} -[{r.type}]-> has two targets ({seen[slot]}, {r.to}); use a context or attrs")
        seen[slot] = r.to


class ContextStore(ABC):
    backend: str
    degraded_reason: str | None = None

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    @abstractmethod
    def write_world(self, batch: WorldBatch) -> dict[str, Any]: ...

    @abstractmethod
    def query_entity(self, id_: str) -> dict[str, Any]: ...

    @abstractmethod
    def list_entities(self, type_: str, where: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def write_memories(self, memories: list[MemoryIn]) -> dict[str, Any]: ...

    @abstractmethod
    def search_memory(self, query: str, top_n: int = 5, tags: list[str] | None = None,
                      threshold: float | None = None) -> list[MemoryHit]: ...

    @abstractmethod
    def feedback(self, memory_id: str, kind: FeedbackKind) -> float: ...


class ContextServiceError(RuntimeError):
    pass


class MeterlessStore(ContextStore):
    backend = "meterless"

    def __init__(self, base_url: str, timeout_s: float = 10.0, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)

    @retry(retry=retry_if_exception_type(httpx.TransportError), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=0.5, max=4), reraise=True)
    def _call(self, method: str, path: str, body: Any = None) -> Any:
        response = self._http.request(method, path, json=body)
        if response.status_code == 400:
            raise ValueError(response.json().get("error", response.text))
        if response.status_code == 404:
            raise KeyError(response.json().get("error", path))
        if response.status_code >= 400:
            raise ContextServiceError(f"{method} {path}: HTTP {response.status_code} {response.text[:300]}")
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health")

    def write_world(self, batch: WorldBatch) -> dict[str, Any]:
        return self._call("POST", "/world/load", batch.payload())

    def query_entity(self, id_: str) -> dict[str, Any]:
        return self._call("POST", "/world/query", {"id": id_})

    def list_entities(self, type_: str, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self._call("POST", "/world/query", {"type": type_, "where": where or {}})["entities"]

    def write_memories(self, memories: list[MemoryIn]) -> dict[str, Any]:
        return self._call("POST", "/mem/write", {"memories": [m.model_dump(exclude_none=True) for m in memories]})

    def search_memory(self, query: str, top_n: int = 5, tags: list[str] | None = None,
                      threshold: float | None = None) -> list[MemoryHit]:
        body = {"query": query, "topN": top_n, "tags": tags or []}
        if threshold is not None:
            body["threshold"] = threshold
        result = self._call("POST", "/mem/search", body)
        return [MemoryHit(relevance=r["relevance"], **{k: r["memory"][k] for k in ("id", "content", "source", "tags", "confidence")})
                for r in result["memories"]]

    def feedback(self, memory_id: str, kind: FeedbackKind) -> float:
        return self._call("POST", "/mem/feedback", {"id": memory_id, "kind": kind})["memory"]["confidence"]

    def close(self) -> None:
        self._http.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT, attrs TEXT NOT NULL,
                                     source TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS relations (from_id TEXT NOT NULL, type TEXT NOT NULL, context TEXT NOT NULL, to_id TEXT NOT NULL,
                                      attrs TEXT, source TEXT NOT NULL, PRIMARY KEY (from_id, type, context));
CREATE TABLE IF NOT EXISTS facts (about TEXT NOT NULL, predicate TEXT NOT NULL, value TEXT NOT NULL, confidence REAL,
                                  source TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (about, predicate, value, at));
CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, source TEXT NOT NULL UNIQUE, tags TEXT NOT NULL,
                                     confidence REAL NOT NULL, provenance TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger (memory_id TEXT NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL, details TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


class SqliteStore(ContextStore):
    """Fallback when context-service is down. Keyword ranking replaces H-MEM's hybrid ranking."""

    backend = "sqlite"
    DEFAULT_THRESHOLD = 0.05

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Parallel graph nodes run on worker threads; the sqlite3 module is built serialized (threadsafety 3).
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def health(self) -> dict[str, Any]:
        count = lambda table: self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: E731
        return {"status": "ok", "backend": self.backend, "entities": count("entities"),
                "relations": count("relations"), "memories": count("memories")}

    def write_world(self, batch: WorldBatch) -> dict[str, Any]:
        check_single_valued(batch.relations)
        source = json.dumps({**batch.source.model_dump(exclude_none=True), "at": batch.source.at or _now()})
        result = {"entities": {"created": 0, "updated": 0, "unchanged": 0},
                  "relations": {"created": 0, "unchanged": 0}, "facts": {"created": 0, "unchanged": 0}}
        with self._db:
            for e in batch.entities:
                id_ = entity_id(e.type, batch.system, e.key)
                row = self._db.execute("SELECT name, attrs FROM entities WHERE id = ?", (id_,)).fetchone()
                attrs = {**(json.loads(row["attrs"]) if row else {}), **e.attrs}
                if row and (e.name is None or row["name"] == e.name) and attrs == json.loads(row["attrs"]):
                    result["entities"]["unchanged"] += 1
                    continue
                self._db.execute("INSERT OR REPLACE INTO entities VALUES (?, ?, ?, ?, ?, ?)",
                                 (id_, e.type, e.name if e.name is not None else (row["name"] if row else None),
                                  json.dumps(attrs), source, _now()))
                result["entities"]["updated" if row else "created"] += 1
            for r in batch.relations:
                for end in (r.from_, r.to):
                    if not self._db.execute("SELECT 1 FROM entities WHERE id = ?", (end,)).fetchone():
                        raise ValueError(f"relation {r.from_} -[{r.type}]-> {r.to}: unknown entity {end}")
                key = (r.from_, r.type, r.context or "")
                row = self._db.execute("SELECT to_id FROM relations WHERE from_id = ? AND type = ? AND context = ?", key).fetchone()
                if row and row["to_id"] == r.to:
                    result["relations"]["unchanged"] += 1
                    continue
                self._db.execute("INSERT OR REPLACE INTO relations VALUES (?, ?, ?, ?, ?, ?)",
                                 (*key, r.to, json.dumps(r.attrs) if r.attrs else None, source))
                result["relations"]["created"] += 1
            for f in batch.facts:
                if not self._db.execute("SELECT 1 FROM entities WHERE id = ?", (f.about,)).fetchone():
                    raise ValueError(f"fact about unknown entity {f.about}")
                at = json.loads(source)["at"]
                cur = self._db.execute("INSERT OR IGNORE INTO facts VALUES (?, ?, ?, ?, ?, ?)",
                                       (f.about, f.predicate, json.dumps(f.value), f.confidence, source, at))
                result["facts"]["created" if cur.rowcount else "unchanged"] += 1
                if cur.rowcount:
                    attrs = json.loads(self._db.execute("SELECT attrs FROM entities WHERE id = ?", (f.about,)).fetchone()[0])
                    self._db.execute("UPDATE entities SET attrs = ? WHERE id = ?", (json.dumps({**attrs, f.predicate: f.value}), f.about))
        return result

    def _entity(self, row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "type": row["type"], "name": row["name"], "attrs": json.loads(row["attrs"])}

    def query_entity(self, id_: str) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM entities WHERE id = ?", (id_,)).fetchone()
        if row is None:
            raise KeyError(f"unknown entity {id_}")
        get = lambda i: self._entity(self._db.execute("SELECT * FROM entities WHERE id = ?", (i,)).fetchone())  # noqa: E731
        out = self._db.execute("SELECT * FROM relations WHERE from_id = ?", (id_,)).fetchall()
        inc = self._db.execute("SELECT * FROM relations WHERE to_id = ?", (id_,)).fetchall()
        facts = self._db.execute("SELECT * FROM facts WHERE about = ? ORDER BY at", (id_,)).fetchall()
        return {
            "entity": self._entity(row),
            "outgoing": [{"type": r["type"], "to": get(r["to_id"]), "context": r["context"] or None,
                          "attrs": json.loads(r["attrs"] or "null")} for r in out],
            "incoming": [{"type": r["type"], "from": get(r["from_id"]), "context": r["context"] or None,
                          "attrs": json.loads(r["attrs"] or "null")} for r in inc],
            "facts": [{"about": f["about"], "predicate": f["predicate"], "value": json.loads(f["value"]),
                       "confidence": f["confidence"], "assertedAt": f["at"]} for f in facts],
        }

    def list_entities(self, type_: str, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows = [self._entity(r) for r in self._db.execute("SELECT * FROM entities WHERE type = ? ORDER BY id", (type_,))]
        return [e for e in rows if all(e["attrs"].get(k) == v for k, v in (where or {}).items())]

    def write_memories(self, memories: list[MemoryIn]) -> dict[str, Any]:
        created, ids = 0, []
        with self._db:
            for m in memories:
                row = self._db.execute("SELECT id, content FROM memories WHERE source = ?", (m.source,)).fetchone()
                if row:
                    if row["content"] != m.content:
                        self._db.execute("UPDATE memories SET content = ?, tags = ? WHERE id = ?",
                                         (m.content, json.dumps(m.tags), row["id"]))
                    ids.append(row["id"])
                    continue
                id_ = f"mem-{self._db.execute('SELECT COUNT(*) FROM memories').fetchone()[0] + 1}"
                self._db.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (id_, m.content, m.source, json.dumps(m.tags), min(max(m.confidence, 0.0), 1.0),
                                  m.provenance.model_dump_json() if m.provenance else None, _now()))
                self._db.execute("INSERT INTO ledger VALUES (?, 'create', ?, NULL)", (id_, _now()))
                created += 1
                ids.append(id_)
        return {"created": created, "unchanged": len(memories) - created, "ids": ids}

    def search_memory(self, query: str, top_n: int = 5, tags: list[str] | None = None,
                      threshold: float | None = None) -> list[MemoryHit]:
        q = _tokens(query)
        hits = []
        for row in self._db.execute("SELECT * FROM memories"):
            row_tags = json.loads(row["tags"])
            if not all(t in row_tags for t in tags or []):
                continue
            words = _tokens(row["content"]) | {t.lower() for t in row_tags}
            relevance = (len(q & words) / len(q | words) if q else 0.0) * row["confidence"]
            if relevance >= (self.DEFAULT_THRESHOLD if threshold is None else threshold):
                hits.append(MemoryHit(id=row["id"], content=row["content"], source=row["source"], tags=row_tags,
                                      confidence=row["confidence"], relevance=relevance))
        hits.sort(key=lambda h: (-h.relevance, h.id))
        return hits[:top_n]

    def feedback(self, memory_id: str, kind: FeedbackKind) -> float:
        row = self._db.execute("SELECT confidence, tags FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown memory {memory_id}")
        confidence = min(max(row["confidence"] + FEEDBACK_DELTAS[kind], 0.0), 1.0)
        tags = json.loads(row["tags"])
        if kind == "wrong" and "review" not in tags:
            tags.append("review")
        with self._db:
            self._db.execute("UPDATE memories SET confidence = ?, tags = ? WHERE id = ?", (confidence, json.dumps(tags), memory_id))
            self._db.execute("INSERT INTO ledger VALUES (?, 'feedback', ?, ?)",
                             (memory_id, _now(), json.dumps({"kind": kind, "delta": FEEDBACK_DELTAS[kind]})))
        return confidence

    def close(self) -> None:
        self._db.close()


def open_context_store(settings: Settings) -> ContextStore:
    """Meterless when configured and reachable; otherwise SQLite, with the reason kept for the decision brief."""
    sqlite_path = settings.context_sqlite_path
    if settings.context_store == "sqlite" or settings.sample_mode:
        return SqliteStore(sqlite_path)
    store = MeterlessStore(settings.context_service_url)
    try:
        store.health()
        return store
    except (httpx.HTTPError, ContextServiceError) as error:
        store.close()
        fallback = SqliteStore(sqlite_path)
        fallback.degraded_reason = f"context-service unreachable at {settings.context_service_url} ({type(error).__name__}); using SQLite"
        return fallback
