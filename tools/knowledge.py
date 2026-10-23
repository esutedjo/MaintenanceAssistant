"""Document knowledge: parse the asset pack's documents into section chunks, embed them, retrieve the chunks
that apply to a case, order them by precedence and decide which mandatory actions apply.

Embeddings are hashed word uni- and bigrams (scikit-learn HashingVectorizer, L2-normalised). They need no model
download or network, so sample mode retrieves exactly like live mode, and the same vectors are stored in
pgvector. Applicability of a mandate is decided here from numbers in the case file, never by an LLM."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import yaml
from sklearn.feature_extraction.text import HashingVectorizer

from settings import ROOT

DIM = 512
CHUNK_COLUMNS = ["chunk_id", "doc_id", "doc_type", "title", "revision", "effective_date", "superseded_by",
                 "superseded_on", "asset_type", "applies_to", "section", "heading", "components", "failure_modes",
                 "tasks", "topic", "mandate", "text", "embedding"]
_VECTORIZER = HashingVectorizer(n_features=DIM, alternate_sign=False, ngram_range=(1, 2), stop_words="english",
                                norm="l2", lowercase=True)
_SECTION = re.compile(r"^## +(\S+) +(.+)$")
_META = re.compile(r"^<!--\s*(\{.*\})\s*-->$")


def embed(texts: list[str]) -> np.ndarray:
    return _VECTORIZER.transform(texts).toarray().astype(np.float32)


def parse_document(path: Path) -> list[dict]:
    """One chunk per `## <section> <heading>` block. List fields are stored comma-separated so they pass through
    Zetaris as plain strings; the mandate is stored as JSON text."""
    raw = path.read_text(encoding="utf-8")
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front)
    chunks, current = [], None
    for line in body.splitlines():
        match = _SECTION.match(line)
        if match:
            current = {"section": match.group(1), "heading": match.group(2).strip(), "meta": {}, "lines": []}
            chunks.append(current)
        elif current is not None and _META.match(line.strip()):
            current["meta"] = yaml.safe_load(_META.match(line.strip()).group(1)) or {}
        elif current is not None:
            current["lines"].append(line)
    rows = []
    for c in chunks:
        m = c["meta"]
        text = " ".join(l.strip() for l in c["lines"] if l.strip())
        rows.append({
            "chunk_id": f"{meta['doc_id']}#{c['section']}", "doc_id": meta["doc_id"], "doc_type": meta["doc_type"],
            "title": meta["title"], "revision": str(meta["revision"]), "effective_date": str(meta["effective_date"]),
            "superseded_by": meta.get("superseded_by") or "", "superseded_on": str(meta.get("superseded_on") or ""),
            "asset_type": meta["asset_type"], "applies_to": ",".join(meta.get("applies_to") or []),
            "section": c["section"], "heading": c["heading"],
            "components": ",".join(m.get("components") or []), "failure_modes": ",".join(m.get("failure_modes") or []),
            "tasks": ",".join(m.get("tasks") or []), "topic": m.get("topic") or "",
            "mandate": json.dumps(m["mandate"]) if m.get("mandate") else "", "text": text,
        })
    return rows


def load_chunks(pack: dict) -> list[dict]:
    spec = pack.get("documents") or {}
    folder = ROOT / spec.get("directory", "")
    rows = [row for name in spec.get("files") or [] for row in parse_document(folder / name)]
    vectors = embed([f"{r['heading']} {r['text']}" for r in rows]) if rows else []
    for row, vector in zip(rows, vectors):
        row["embedding"] = [round(float(v), 6) for v in vector]
    return rows


def _split(value) -> list[str]:
    return [v for v in str(value or "").split(",") if v]


def _vector(value) -> np.ndarray | None:
    """pgvector arrives through Zetaris as text '[0.1,...]'; Parquet gives a list or array."""
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value) if value.strip() else None
    return None if value is None else np.asarray(value, dtype=np.float32)


@dataclass
class Retrieved:
    chunk: dict
    relevance: float
    precedence: int
    mandate: dict | None = None
    mandate_reason: str | None = None
    overridden_by: str | None = None
    excluded: str | None = None

    @property
    def label(self) -> str:
        return f"{self.chunk['doc_id']} {self.chunk['section']}"


@dataclass
class Retrieval:
    applicable: list[Retrieved]
    excluded: list[Retrieved] = field(default_factory=list)
    query: str = ""

    @property
    def mandates(self) -> list[Retrieved]:
        return [r for r in self.applicable if r.mandate_reason]


def in_effect(chunk: dict, on: date) -> str | None:
    """None when the chunk's revision is in effect on the date, else the reason it is not."""
    if date.fromisoformat(str(chunk["effective_date"])[:10]) > on:
        return f"not yet effective ({chunk['effective_date']})"
    superseded_on = str(chunk.get("superseded_on") or "")
    if chunk.get("superseded_by") and (not superseded_on or date.fromisoformat(superseded_on[:10]) <= on):
        return f"superseded by {chunk['superseded_by']}"
    return None


def mandate_applies(mandate: dict, fault_modes: list[tuple[str, float]], rul_lower: float | None) -> str | None:
    """Reason text when the mandate's conditions hold for this case, else None. Every condition must hold."""
    wanted = set(mandate.get("failure_modes") or [])
    hits = [(m, p) for m, p in fault_modes if m in wanted and p >= mandate.get("min_probability", 0.0)]
    if wanted and not hits:
        return None
    below = mandate.get("rul_lower_below")
    if below is not None and (rul_lower is None or rul_lower >= below):
        return None
    parts = [f"{m} p={p:.2f} >= {mandate.get('min_probability', 0.0)}" for m, p in hits[:1]]
    if below is not None:
        parts.append(f"RUL lower bound {rul_lower:.0f} < {below} cycles")
    return "; ".join(parts)


def mandate_conditions(mandate: dict) -> str:
    parts = []
    if mandate.get("failure_modes"):
        parts.append(f"{' or '.join(mandate['failure_modes'])} with p >= {mandate.get('min_probability', 0.0)}")
    if mandate.get("rul_lower_below") is not None:
        parts.append(f"RUL lower bound < {mandate['rul_lower_below']} cycles")
    return " and ".join(parts) or "always"


def retrieve(chunks: list[dict], pack: dict, *, variant: str | None, fault_modes: list[tuple[str, float]],
             rul_lower: float | None, on: date, extra_terms: list[str] = ()) -> Retrieval:
    """Filter to the asset type, fleet variant, relevant components and revision in effect; rank the rest by
    similarity to a query built from the findings; keep the top_k plus every chunk whose mandate applies."""
    spec = pack.get("documents") or {}
    order = {t: i for i, t in enumerate(spec.get("precedence") or [])}
    top_k = int(spec.get("top_k", 4))
    modes = [m for m, _ in fault_modes]
    components = {pack["failure_modes"][m]["component"] for m in modes if m in pack.get("failure_modes", {})}
    labels = [pack["failure_modes"][m]["label"] for m in modes if m in pack.get("failure_modes", {})]
    names = [pack["components"][c] for c in components if c in pack.get("components", {})]
    query = " ".join([*labels, *names, *extra_terms])
    q = embed([query])[0]

    candidates, excluded = [], []
    for chunk in chunks:
        r = Retrieved(chunk, 0.0, order.get(chunk["doc_type"], len(order)))
        if chunk["asset_type"] != pack["asset_type"]:
            continue
        reason = in_effect(chunk, on)
        if not reason and variant and variant not in _split(chunk["applies_to"]):
            reason = f"not applicable to {variant}"
        chunk_modes, chunk_comps = set(_split(chunk["failure_modes"])), set(_split(chunk["components"]))
        if not reason and not (chunk_modes & set(modes) or chunk_comps & components):
            reason = "different component"
        if reason:
            r.excluded = reason
            if chunk_modes & set(modes) or chunk_comps & components:
                excluded.append(r)
            continue
        vector = _vector(chunk.get("embedding"))
        if vector is None:
            vector = embed([f"{chunk['heading']} {chunk['text']}"])[0]
        r.relevance = round(float(vector @ q), 3)
        if chunk.get("mandate"):
            r.mandate = json.loads(chunk["mandate"]) if isinstance(chunk["mandate"], str) else chunk["mandate"]
            r.mandate_reason = mandate_applies(r.mandate, fault_modes, rul_lower)
        candidates.append(r)

    candidates.sort(key=lambda r: -r.relevance)
    keep = candidates[:top_k] + [r for r in candidates[top_k:] if r.mandate_reason]
    _apply_precedence(keep)
    keep.sort(key=lambda r: (not r.mandate_reason, r.precedence, -r.relevance))
    return Retrieval(keep, excluded, query)


def _apply_precedence(items: list[Retrieved]) -> None:
    """Within a topic, the highest-precedence (then latest) document wins; the others are marked overridden."""
    by_topic: dict[str, list[Retrieved]] = {}
    for r in items:
        if r.chunk.get("topic"):
            by_topic.setdefault(r.chunk["topic"], []).append(r)
    for group in by_topic.values():
        group.sort(key=lambda r: (r.precedence, -int(str(r.chunk["effective_date"]).replace("-", "")[:8])))
        for loser in group[1:]:
            loser.overridden_by = group[0].label
