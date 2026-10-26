"""Knowledge agent: the document sections that apply to this engine and finding, in precedence order, and the
mandatory actions they impose. Runs after the specialists, because what applies depends on the fault mode and
the remaining-life lower bound. Mandates are decided in code (tools.knowledge); agents may not remove them.
When a gate finds a mandate missing, uncited or late, the planner routes it back here to bind it for the plan."""

from datetime import timedelta

from agents.runtime import NodeResult, RunContext, node, run_tasks
from assurance.escalation import needs_approval
from casefile.context_store import entity_id
from casefile.schemas import CaseFile, Claim, DocumentFinding, EvidenceLedger, Findings, Task
from casefile.world import _task_from_entity, mandated_for_mode, query_graph
from tools.knowledge import mandate_conditions, retrieve

# Fault modes below this probability do not pull in their documents (the top mode always does).
MIN_MODE_PROBABILITY = 0.1



def _rul_lower(state: CaseFile) -> tuple[float | None, list[str]]:
    a, b = state.findings.rul_ml, state.findings.rul_trend
    if a is None:
        return None, []
    lower = min(a.lower, b.lower if b else a.lower)
    return lower, [a.evidence_id, *([b.evidence_id] if b else [])]


def _query_terms(state: CaseFile, pack: dict) -> list[str]:
    a, b = state.findings.rul_ml, state.findings.rul_trend
    if a is None:
        return []
    diverge = b is not None and abs(a.rul - b.rul) > pack["thresholds"]["rul_divergence_cycles"]
    if diverge:
        return ["borescope", "estimates differ", "do not average"]
    if a.at_cap or (b is not None and b.at_cap):
        return ["trend monitoring", "routine inspection interval"]
    return ["inspection", "replace", "removal"]


@node("knowledge", "retrieve_documents", lambda s: f"asset={s.asset.asset_id if s.asset else None} "
                                                   f"modes={[m.failure_mode for m in s.findings.fault_modes[:2]]}")
def knowledge(state: CaseFile, ctx: RunContext) -> NodeResult:
    asset, pack = state.asset, ctx.pack
    result = ctx.data.doc_chunks(asset.asset_type)
    chunks = result.frame.to_dict("records")
    modes = [(m.failure_mode, m.probability) for i, m in enumerate(state.findings.fault_modes)
             if i == 0 or m.probability >= MIN_MODE_PROBABILITY]
    lower, rul_ids = _rul_lower(state)
    found = retrieve(chunks, pack, variant=asset.variant, fault_modes=modes, rul_lower=lower,
                     on=ctx.settings.reference_time.date(), extra_terms=_query_terms(state, pack))

    ledger = EvidenceLedger("knowledge", state.evidence)
    fault_ids = [m.evidence_id for m in state.findings.fault_modes[:1]]
    excluded = [{"chunk": r.label, "reason": r.excluded} for r in found.excluded]
    excluded_text = ", ".join(f"{e['chunk']} ({e['reason']})" for e in excluded)
    ledger.add("query", result.source,
               f"{len(chunks)} document sections for {asset.asset_type}; {len(found.applicable)} apply"
               + (f"; excluded: {excluded_text}" if excluded else ""),
               {"query": found.query, "sql": result.query, "sections": len(chunks), "kept": len(found.applicable),
                "excluded": excluded, "rul_lower_cycles": lower, "fault_modes": modes},
               degraded=result.degraded)

    documents, claims = [], []
    for r in found.applicable:
        c = r.chunk
        mandate = r.mandate if r.mandate_reason else None
        if mandate:
            status = f"MANDATORY: {mandate['action']} (applies: {r.mandate_reason})"
        elif r.mandate:
            status = (f"mandatory only when {mandate_conditions(r.mandate)}; not met here"
                      + (f" (RUL lower bound {lower:.0f})" if lower is not None else ""))
        elif r.overridden_by:
            status = f"overridden by {r.overridden_by} on {c['topic']}"
        else:
            status = f"relevance {r.relevance:.2f}"
        doc_type = c["doc_type"].replace("_", " ")
        evidence = ledger.add(
            "document", f"{result.source}#{c['chunk_id']}",
            f"{c['doc_id']} {c['section']} {c['heading']} [{doc_type}, rev {c['revision']}, "
            f"effective {str(c['effective_date'])[:10]}]: {status}",
            {**{k: (str(v)[:10] if k in ("effective_date", "superseded_on") else v)
                for k, v in c.items() if k != "embedding"},
             "relevance": r.relevance, "precedence": r.precedence, "mandate": mandate,
             "mandate_reason": r.mandate_reason, "overridden_by": r.overridden_by},
            degraded=result.degraded)
        documents.append(DocumentFinding(
            doc_id=c["doc_id"], doc_type=c["doc_type"], section=c["section"], heading=c["heading"],
            revision=str(c["revision"]), effective_date=str(c["effective_date"])[:10], precedence=r.precedence,
            relevance=r.relevance, failure_modes=(mandate or {}).get("failure_modes")
            or [m for m in str(c["failure_modes"]).split(",") if m], mandatory=bool(mandate), required_task=(mandate or {}).get("task"),
            within_cycles=(mandate or {}).get("within_cycles"), action=(mandate or {}).get("action"),
            overridden_by=r.overridden_by, evidence_id=evidence.id))
        if mandate:
            claims.append(Claim(
                text=(f"{c['doc_id']} {c['section']} ({doc_type}) requires FMEA task {mandate['task']} within "
                      f"{mandate['within_cycles']} cycles: {mandate['action']}."),
                evidence_ids=[evidence.id, *rul_ids, *fault_ids], confidence="high"))

    by_label = {f"{d.doc_id} {d.section}": d for d in documents}
    for d in documents:
        winner = by_label.get(d.overridden_by or "")
        if winner:
            approval = "; SB recommended; adopting it needs engineering approval" if needs_approval(winner) else ""
            claims.append(Claim(
                text=(f"{winner.doc_id} {winner.section} ({winner.doc_type.replace('_', ' ')}) takes precedence over "
                      f"{d.doc_id} {d.section} ({d.doc_type.replace('_', ' ')}) on the same topic{approval}."),
                evidence_ids=[winner.evidence_id, d.evidence_id], confidence="high"))
    if documents:
        claims.append(Claim(text="Applicable documents, highest precedence first: "
                                 + ", ".join(f"{d.doc_id} {d.section}" for d in documents) + ".",
                            evidence_ids=[d.evidence_id for d in documents], confidence="medium"))

    mandates = [d for d in documents if d.mandatory]
    summary = (f"{len(documents)} sections apply; {len(mandates)} mandatory"
               + (f" ({', '.join(d.doc_id for d in mandates)})" if mandates else "")
               + (f"; {len(found.excluded)} excluded" if found.excluded else ""))
    return NodeResult({"findings": Findings(documents=documents, claims=claims), "evidence": ledger.records},
                      summary, target_agent="maint_planner")


def bind_mandates(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Repair for a missing, uncited or late mandate: re-read each mandatory section and bind it for the plan: the
    task the document requires (from the World Model's requires edge and the task node, so the planner does not
    depend on its own catalogue), the date it must be done by, and the evidence the plan must cite."""
    pack, asset = ctx.pack, state.asset
    cycles_per_day = pack["thresholds"]["cycles_per_day"]
    lines, claims = [], []
    for doc in [d for d in state.findings.documents if d.mandatory and d.required_task]:
        links = [m for mode in doc.failure_modes for m in mandated_for_mode(ctx.store, pack, mode, asset.variant)
                 if m["doc_id"] == doc.doc_id and m["section"] == doc.section]
        task_id = links[0]["task"] if links else doc.required_task
        node_ = query_graph(ctx.store, entity_id("task", pack["asset_type"], task_id))
        record = _task_from_entity(node_["entity"]) if node_ and node_.get("entity") else None
        if record is None:
            lines.append(f"{doc.doc_id} {doc.section}: the World Model has no task node for {task_id}; not bound")
            continue
        comply_by = ctx.settings.reference_time.date() + timedelta(days=int((doc.within_cycles or 0) / cycles_per_day))
        source = "world" if links else "document"
        evidence = ledger.add(
            "rule", f"{ctx.store.backend}:world/{doc.doc_id}#{doc.section}",
            (f"Binding for the plan: {doc.doc_id} {doc.section} requires {task_id} ({record['task']}) within "
             f"{doc.within_cycles} cycles, by {comply_by}; the plan must include it and cite {doc.evidence_id}"),
            {"binding": True, "doc_id": doc.doc_id, "section": doc.section, "doc_evidence": doc.evidence_id,
             "task_ids": [task_id], "tasks": [record], "within_cycles": doc.within_cycles,
             "comply_by": comply_by.isoformat(), "resolved_from": source},
            degraded=ctx.store.degraded_reason)
        claims.append(Claim(text=(f"{doc.doc_id} {doc.section} binds {task_id} within {doc.within_cycles} cycles "
                                  f"(by {comply_by}); it was missing from or uncited in the plan."),
                            evidence_ids=[evidence.id, doc.evidence_id], confidence="high"))
        lines.append(f"{evidence.id}: bound {task_id} from {doc.doc_id} {doc.section} by {comply_by}")
    return "; ".join(lines) or "no mandatory section to bind", Findings(claims=claims) if claims else None


@node("knowledge", "repair", lambda s: f"tasks={[t.kind for t in s.pending('knowledge')]}")
def knowledge_task(state: CaseFile, ctx: RunContext) -> NodeResult:
    return run_tasks(state, ctx, "knowledge", {"repair": bind_mandates})
