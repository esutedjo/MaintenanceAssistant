"""Knowledge agent: the document sections that apply to this engine and finding, in precedence order, and the
mandatory actions they impose. Runs after the specialists, because what applies depends on the fault mode and
the remaining-life lower bound. Mandates are decided in code (tools.knowledge); agents may not remove them."""

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile, Claim, DocumentFinding, EvidenceLedger, Findings
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
            claims.append(Claim(
                text=(f"{winner.doc_id} {winner.section} ({winner.doc_type.replace('_', ' ')}) takes precedence over "
                      f"{d.doc_id} {d.section} ({d.doc_type.replace('_', ' ')}) on the same topic."),
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
