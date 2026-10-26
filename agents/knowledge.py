"""Knowledge agent: the document sections that apply to this engine and finding, in precedence order, and the
mandatory actions they impose. Runs after the specialists, because what applies depends on the fault mode and
the remaining-life lower bound. Mandates are decided in code (tools.knowledge); agents may not remove them.
For each inspection finding it retrieves the limit section in effect for that kind of inspection and module and cites
it; code compares the finding with it (tools/inspections.py).
When a gate finds a mandate missing, uncited or late, the planner routes it back here to bind it for the plan."""

from datetime import timedelta

from agents.runtime import NodeResult, RunContext, node, run_tasks
from assurance.escalation import needs_approval
from casefile.context_store import entity_id
from casefile.schemas import (
    AFTER_KNOWLEDGE, CaseFile, Claim, DocumentFinding, EvidenceLedger, Findings, InspectionFinding, Task,
)
from casefile.world import _task_from_entity, mandated_for_mode, query_graph
from tools.data_access import SnapshotSource
from tools.inspections import (
    RESULT_TEXT, assess, is_limit_section, label, limit_section, mark_current, parse_limits, where,
)
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
    all_chunks = result.frame.to_dict("records")
    chunks = [c for c in all_chunks if not is_limit_section(c)]     # limit tables are looked up per finding
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
    assessments, limit_docs, limit_claims, degraded = compare_inspections(state, ctx, ledger, all_chunks, result)
    if assessments:
        documents += limit_docs
        claims += limit_claims
        summary += "; inspections: " + ", ".join(f"{a.evidence_id} {a.result.replace('_', ' ')}"
                                                 + ("" if a.current else " (superseded)") for a in assessments)
    update = {"findings": Findings(documents=documents, inspections=assessments, claims=claims),
              "evidence": ledger.records}
    if degraded:
        update["degraded"] = degraded
    check = any(t.kind in AFTER_KNOWLEDGE for t in state.pending("diagnostics"))      # runs next, then the plan
    return NodeResult(update, summary, target_agent="diagnostics" if check else "maint_planner")


def compare_inspections(state: CaseFile, ctx: RunContext, ledger: EvidenceLedger, chunks: list[dict], result):
    """For each inspection finding (EV-INS), the limit section in effect for its kind and module, cited as document
    evidence, and the comparison done in code (tools/inspections.py). The knowledge_pg table predates the limit
    sections until `uv run task ingest-docs` runs; then they come from the committed snapshot, and the brief says so."""
    # A finding the data agent held back (cycle after the last telemetry cycle, or a future date) is never assessed,
    # so it can neither become current nor supersede a real finding.
    entered = [e for e in state.evidence.values() if e.kind == "inspection" and not e.content.get("quarantined")]
    if not entered:
        return [], [], [], []
    pack, asset = ctx.pack, state.asset
    on = ctx.settings.reference_time.date()
    limits, source, degraded = [c for c in chunks if is_limit_section(c)], result.source, result.degraded
    notes = []
    if not limits:
        snap = SnapshotSource().doc_chunks(asset.asset_type)
        limits, source = [c for c in snap.frame.to_dict("records") if is_limit_section(c)], snap.source
        degraded = (f"limit sections are not in {result.source}; read from {snap.source} "
                    "(run uv run task ingest-docs)")
        notes.append(f"Inspection limits read from the snapshot: {result.source} has no limit sections")
    cited: dict[str, tuple] = {}
    documents, claims, pairs = [], [], []
    order = {t: i for i, t in enumerate((pack.get("documents") or {}).get("precedence") or [])}
    for item in entered:
        finding = InspectionFinding.model_validate(item.content["finding"])
        chunk, excluded = limit_section(limits, pack, finding.kind, finding.module, on, asset.variant)
        if chunk is not None and chunk["chunk_id"] not in cited:
            table = parse_limits(chunk["text"])
            rows = "; ".join(f"{r['id']} {r['name']} serviceable up to {r['serviceable']:g}, reject above "
                             f"{r['reject']:g} {r['unit']}" for r in table["rows"])
            doc_type = chunk["doc_type"].replace("_", " ")
            evidence = ledger.add(
                "document", f"{source}#{chunk['chunk_id']}",
                (f"{label(chunk)} {chunk['heading']} [{doc_type}, rev {chunk['revision']}, effective "
                 f"{str(chunk['effective_date'])[:10]}]: {rows}; next inspection {table['routine_cycles']} cycles "
                 f"within limits, re-inspect within {table['reinspect_cycles']} cycles at the limit"),
                {**{k: (str(v)[:10] if k in ("effective_date", "superseded_on") else v)
                    for k, v in chunk.items() if k != "embedding"},
                 "limits": table["rows"], "routine_cycles": table["routine_cycles"],
                 "reinspect_cycles": table["reinspect_cycles"], "not_used": excluded},
                degraded=degraded)
            cited[chunk["chunk_id"]] = (evidence.id, table)
            documents.append(DocumentFinding(
                doc_id=chunk["doc_id"], doc_type=chunk["doc_type"], section=chunk["section"],
                heading=chunk["heading"], revision=str(chunk["revision"]),
                effective_date=str(chunk["effective_date"])[:10],
                precedence=order.get(chunk["doc_type"], len(order)), relevance=1.0, evidence_id=evidence.id))
        if chunk is not None:
            limit_id, table = cited[chunk["chunk_id"]]
        else:
            default = (pack.get("inspections") or {}).get("default_reinspect_cycles")
            limit_id = ledger.add(
                "rule", f"asset_pack:{pack['asset_type']}/inspections",
                (f"No limit section in the documents covers a {finding.kind.replace('_', ' ')} inspection of the "
                 f"{finding.module.replace('_', ' ')}; the inspector's result stands (inspector-classified); "
                 f"re-inspection at the limit within {default} cycles (asset pack default)"),
                {"kind": finding.kind, "module": finding.module, "default_reinspect_cycles": default,
                 "not_used": excluded}).id
            table = None
        assessment = assess(finding, item.id, limit_evidence_id=limit_id, section=chunk, limits=table, pack=pack,
                            fresh=bool(item.content.get("fresh")))
        pairs.append((assessment, finding))
    assessments = mark_current(pairs)
    for a in assessments:
        claims.append(Claim(text=assessment_text(a, state.evidence[a.evidence_id].content["finding"], pack),
                            evidence_ids=[a.evidence_id, a.limit_evidence_id], confidence="high"))
    return assessments, documents, claims, notes


def assessment_text(a, finding: dict, pack: dict) -> str:
    """One sentence per finding: what was measured, against which limit, and the result (or who classified it)."""
    head = f"Inspection finding {a.evidence_id} ({where(finding, pack)}, cycle {a.cycle})"
    if a.classified_by == "limit":
        row, m = a.limit, finding["measurement"]
        relation = {"within": f"within the serviceable limit {row['serviceable']:g} {row['unit']}",
                    "at_limit": (f"above the serviceable limit {row['serviceable']:g} {row['unit']} and not above "
                                 f"the reject limit {row['reject']:g} {row['unit']}"),
                    "beyond": f"above the reject limit {row['reject']:g} {row['unit']}"}[a.result]
        text = (f"{head}: {row['name']} {m['value']:g} {m['unit']} is {relation} of {a.reference}: "
                f"{RESULT_TEXT[a.result]}")
    else:
        text = (f"{head} is {RESULT_TEXT[a.result]} as the inspector classified it (inspector-classified: "
                f"{a.note})")
    if not a.current:
        text += f"; superseded by the later finding {a.superseded_by}"
    return text + "."


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
