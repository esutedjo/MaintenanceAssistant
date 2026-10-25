"""Read the World Model: eligible work, sensor-component paths, mandated tasks, asset facts.

The asset pack and FMEA file are ingest sources. Runtime answers come from the graph."""

from __future__ import annotations

from typing import Any

from casefile.context_store import ContextStore, EntityIn, FactIn, Source, WorldBatch, entity_id
from casefile.schemas import CaseFile

Decision = str


def entity_key(ident: str) -> str:
    parts = ident.split(":", 2)
    return parts[2] if len(parts) == 3 else ident


def context_ok(edge: dict[str, Any], variant: str | None, mode: str | None = None) -> bool:
    """Edges with no context apply to every variant. `addresses`/`affects` use the variant;
    `applies_to` uses `{variant}|{mode}` so one document can link to two faults in the same fleet."""
    ctx = edge.get("context") or ""
    if not ctx or not variant:
        return True
    return ctx == variant or (mode is not None and ctx in {f"{variant}|{mode}", mode})


def query_graph(store: ContextStore, ident: str) -> dict[str, Any] | None:
    try:
        return store.query_entity(ident)
    except Exception:
        return None


def _task_from_entity(entity: dict[str, Any]) -> dict[str, Any]:
    attrs = entity.get("attrs") or {}
    return {
        "fmea_id": entity_key(entity.get("id") or ""),
        "task": entity.get("name") or attrs.get("task") or entity_key(entity.get("id") or ""),
        "task_type": attrs.get("task_type") or "inspect",
        "trigger_severity": attrs.get("trigger_severity") or "minor",
        "cause": attrs.get("cause"),
        "effect": attrs.get("effect"),
        "detection": attrs.get("detection"),
        "parts": list(attrs.get("parts") or []),
    }


def tasks_addressing(store: ContextStore, pack: dict, mode: str, variant: str | None) -> list[dict[str, Any]]:
    """Tasks the graph links to this failure mode in this fleet variant. Empty if the lookup fails."""
    graph = query_graph(store, entity_id("failure_mode", pack["asset_type"], mode))
    if graph is None:
        return []
    prefix = f"task:{pack['asset_type']}:"
    tasks = []
    for edge in graph.get("incoming") or []:
        if edge.get("type") != "addresses" or not context_ok(edge, variant):
            continue
        entity = edge.get("from") or {}
        if str(entity.get("id") or "").startswith(prefix):
            tasks.append(_task_from_entity(entity))
    tasks.sort(key=lambda t: t["fmea_id"])
    return tasks


def task_ids_for(store: ContextStore, pack: dict, mode: str, variant: str | None) -> list[str]:
    return [t["fmea_id"] for t in tasks_addressing(store, pack, mode, variant)]


def mode_component(store: ContextStore, pack: dict, mode: str, variant: str | None) -> str | None:
    graph = query_graph(store, entity_id("failure_mode", pack["asset_type"], mode))
    if graph is None:
        return (pack.get("failure_modes") or {}).get(mode, {}).get("component")
    prefix = f"component:{pack['asset_type']}:"
    for edge in graph.get("outgoing") or []:
        if edge.get("type") != "affects" or not context_ok(edge, variant):
            continue
        ident = str((edge.get("to") or {}).get("id") or "")
        if ident.startswith(prefix):
            return entity_key(ident)
    return None


def sensor_component(store: ContextStore, pack: dict, sensor: str) -> str | None:
    graph = query_graph(store, entity_id("sensor", pack["asset_type"], sensor))
    if graph is None:
        return (pack.get("sensors") or {}).get(sensor, {}).get("component")
    prefix = f"component:{pack['asset_type']}:"
    for edge in graph.get("outgoing") or []:
        if edge.get("type") != "monitors":
            continue
        ident = str((edge.get("to") or {}).get("id") or "")
        if ident.startswith(prefix):
            return entity_key(ident)
    return (pack.get("sensors") or {}).get(sensor, {}).get("component")


def discriminating_sensors(store: ContextStore | None, pack: dict, mode: str) -> list[str]:
    if store is not None:
        graph = query_graph(store, entity_id("failure_mode", pack["asset_type"], mode))
        attrs = ((graph or {}).get("entity") or {}).get("attrs") or {}
        listed = attrs.get("discriminating_sensors")
        if listed:
            return list(listed)
    from models.fault_mode import discriminating
    variants = [m for modes in pack.get("variants", {}).values() for m in modes]
    return list(discriminating(pack, sorted(set(variants))).get(mode, {}))


def mandated_for_mode(store: ContextStore, pack: dict, mode: str, variant: str | None) -> list[dict[str, Any]]:
    """Document sections the graph links to this fault (`applies_to`) and the task each `requires`."""
    graph = query_graph(store, entity_id("failure_mode", pack["asset_type"], mode))
    if graph is None:
        return []
    prefix = f"document:{pack['asset_type']}:"
    out = []
    for edge in graph.get("incoming") or []:
        if edge.get("type") != "applies_to" or not context_ok(edge, variant, mode):
            continue
        entity = edge.get("from") or {}
        if not str(entity.get("id") or "").startswith(prefix):
            continue
        attrs = entity.get("attrs") or {}
        task_id = attrs.get("task")
        if not task_id:
            doc = query_graph(store, entity["id"])
            for req in (doc or {}).get("outgoing") or []:
                if req.get("type") == "requires":
                    task_id = entity_key((req.get("to") or {}).get("id") or "")
                    break
        if not task_id:
            continue
        out.append({
            "doc_id": attrs.get("doc_id") or entity_key(entity.get("id") or ""),
            "section": attrs.get("section"),
            "doc_type": attrs.get("doc_type"),
            "task": task_id,
            "action": attrs.get("action"),
            "within_cycles": attrs.get("within_cycles"),
            "chunk_id": entity_key(entity.get("id") or ""),
        })
    out.sort(key=lambda d: (d["doc_id"], d["section"] or ""))
    return out


def asset_state(store: ContextStore, pack: dict, asset_id: str) -> dict[str, Any]:
    graph = query_graph(store, entity_id("asset", pack["asset_type"], asset_id))
    if graph is None:
        return {}
    attrs = ((graph.get("entity") or {}).get("attrs") or {})
    return {k: attrs[k] for k in ("last_decision", "last_fault", "last_bound_cycles", "last_action",
                                  "pack_version", "last_case_id") if k in attrs}


def fmea_extra(world_ids: list[str], fmea_ids: list[str]) -> list[str]:
    """Task IDs the live FMEA table lists for this fault that the graph does not."""
    return sorted(set(fmea_ids) - set(world_ids))


def signoff_batch(pack: dict, case: CaseFile, decision: Decision) -> WorldBatch:
    """Facts on this asset after engineer sign-off. Creates the asset node if it is missing."""
    system = pack["asset_type"]
    asset_id = case.asset.asset_id if case.asset else "unknown"
    ident = entity_id("asset", system, asset_id)
    rec = case.recommendation
    a, b = case.findings.rul_ml, case.findings.rul_trend
    lower = None if a is None else min(a.lower, b.lower if b else a.lower)
    fault = case.findings.fault_modes[0].failure_mode if case.findings.fault_modes else None
    entities = [EntityIn(type="asset", key=asset_id, name=asset_id,
                         attrs={"asset_type": system, "variant": case.asset.variant if case.asset else None})]
    type_id = entity_id("asset_type", system, system)
    facts = [
        FactIn(about=ident, predicate="last_decision", value=decision, confidence=1.0),
        FactIn(about=ident, predicate="last_action", value=(rec.action if rec else ""), confidence=1.0),
        FactIn(about=ident, predicate="last_case_id", value=case.case_id, confidence=1.0),
        FactIn(about=ident, predicate="pack_version", value=pack.get("version"), confidence=1.0),
    ]
    if fault:
        facts.append(FactIn(about=ident, predicate="last_fault", value=fault, confidence=1.0))
    if lower is not None:
        facts.append(FactIn(about=ident, predicate="last_bound_cycles", value=round(float(lower), 1), confidence=1.0))
    from casefile.context_store import RelationIn
    rels = [RelationIn(**{"from": ident, "type": "instance_of", "to": type_id})]
    source = Source(kind="agent", by="engineer-signoff", runId=case.case_id)
    return WorldBatch(system=system, source=source, entities=entities, relations=rels, facts=facts)
