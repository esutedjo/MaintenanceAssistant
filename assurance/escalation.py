"""Deterministic escalation rules. Thresholds come from the asset pack. Never writes the recommendation."""

from casefile.schemas import CaseFile, DocumentFinding, Escalation

# Document types whose compliance is recommended unless a mandate in them fires. One that wins a topic over a manual
# by precedence is not adopted silently: the plan keeps the manual and an escalation asks engineering to decide.
RECOMMENDED_TYPES = {"service_bulletin"}


def needs_approval(doc: DocumentFinding) -> bool:
    return doc.doc_type in RECOMMENDED_TYPES and not doc.mandatory


def needs_acknowledgement(severity: str | None, rule: str | None = None) -> bool:
    """Urgent, warning and mandatory-document escalations need a tick before approval; an info escalation is a note
    the sign-off records as seen, so approval is not buried in ticks for advice that changes nothing."""
    return severity != "info" or rule == "document_mandate"


def pending_adoptions(case: CaseFile) -> list[tuple[DocumentFinding, DocumentFinding]]:
    """(recommended document, the document it would replace) for each topic a non-mandatory SB wins by precedence."""
    by_label = {f"{d.doc_id} {d.section}": d for d in case.findings.documents}
    return [(by_label[d.overridden_by], d) for d in case.findings.documents
            if d.overridden_by in by_label and needs_approval(by_label[d.overridden_by])]


def remaining_shorter_than_wait(rul_days: float, lead_days: float, slot_days: float) -> bool:
    """True when the RUL lower bound (days) cannot cover parts lead time plus days to the next free slot."""
    return rul_days < lead_days + slot_days


def parts_wait_days(parts) -> int:
    """Longest lead time among parts with too little stock; parts on hand do not wait."""
    return max((int(p.lead_time_days) for p in parts if p.available < p.qty), default=0)


def _rul_days(case: CaseFile, pack: dict) -> tuple[float | None, list[str]]:
    a, b = case.findings.rul_ml, case.findings.rul_trend
    if a is None:
        return None, []
    lower = min(a.lower, b.lower if b else a.lower)
    return lower / pack["thresholds"]["cycles_per_day"], [a.evidence_id, *( [b.evidence_id] if b else [])]


def rul_divergence(case: CaseFile, pack: dict) -> Escalation | None:
    a, b = case.findings.rul_ml, case.findings.rul_trend
    if a is None or b is None:
        return None
    gap = abs(a.rul - b.rul)
    limit = pack["thresholds"]["rul_divergence_cycles"]
    if gap <= limit:
        return None
    return Escalation(
        rule="rul_divergence", severity="warning",
        message=f"RUL A ({a.rul:.1f}) and RUL B ({b.rul:.1f}) differ by {gap:.1f} cycles (limit {limit:.0f}).",
        actions=["Recommend borescope inspection", "Do not average the two remaining-life estimates"],
        evidence_ids=[a.evidence_id, b.evidence_id],
    )


def ambiguous_fault(case: CaseFile, pack: dict) -> Escalation | None:
    modes = case.findings.fault_modes
    if len(modes) < 2:
        return None
    gap = abs(modes[0].probability - modes[1].probability)
    limit = pack["thresholds"]["ambiguous_fault_probability"]
    if gap >= limit:
        return None
    labels = [pack["failure_modes"][m.failure_mode]["label"] for m in modes[:2]]
    return Escalation(
        rule="ambiguous_fault", severity="warning",
        message=f"{labels[0]} ({modes[0].probability:.2f}) and {labels[1]} ({modes[1].probability:.2f}) are within {limit}.",
        actions=["Inspection targeting both modules"],
        evidence_ids=[modes[0].evidence_id, modes[1].evidence_id],
    )


def rul_below_lead_time(case: CaseFile, pack: dict) -> Escalation | None:
    rec = case.recommendation
    if rec is None:
        return None
    days, evidence_ids = _rul_days(case, pack)
    if days is None:
        return None
    lead = parts_wait_days(rec.parts)
    slot_days = 0
    for evidence in reversed(list(case.evidence.values())):      # a re-queried slot list supersedes the first
        if evidence.content.get("days_to_first_free") is not None:
            slot_days = int(evidence.content["days_to_first_free"])
            evidence_ids = [*evidence_ids, evidence.id]
            break
    if not remaining_shorter_than_wait(days, lead, slot_days):
        return None
    extra = [p.evidence_ids[0] for p in rec.parts if p.evidence_ids]
    return Escalation(
        rule="rul_below_lead_time", severity="urgent",
        message=(f"RUL lower bound is {days:.1f} days; lead time of short parts {lead} d plus next free slot "
                 f"{slot_days} d."),
        actions=["Propose expedite", "Propose engine swap", "Propose derate"],
        evidence_ids=[*evidence_ids, *extra][:8] or evidence_ids,
    )


def document_mandates(case: CaseFile, pack: dict) -> list[Escalation]:
    """Mandatory document actions are listed before every other escalation; no agent can remove them."""
    return [Escalation(
        rule="document_mandate", severity="urgent",
        message=(f"{d.doc_id} {d.section} ({d.doc_type.replace('_', ' ')}) requires {d.required_task} "
                 f"within {d.within_cycles} cycles: {d.action}"),
        actions=[d.action or f"Perform {d.required_task}", "Mandatory: cannot be removed or deferred by the agents"],
        evidence_ids=[d.evidence_id],
    ) for d in case.findings.documents if d.mandatory]


def serious_data_issues(case: CaseFile) -> list[tuple[str, dict]]:
    """(evidence ID, issue) for every serious input-plausibility issue in the case."""
    return [(e.id, issue) for e in case.evidence.values() for issue in e.content.get("issues") or []
            if isinstance(issue, dict) and issue.get("severity") == "serious"]


def knowledge_drift(case: CaseFile, pack: dict) -> Escalation | None:
    """Live FMEA rows that the World Model does not link to this fault."""
    extras = []
    evidence_ids = []
    for evidence in case.evidence.values():
        extra = evidence.content.get("fmea_drift_extra") or []
        if extra:
            extras.extend(extra)
            evidence_ids.append(evidence.id)
    if not extras:
        return None
    listed = ", ".join(dict.fromkeys(extras))
    return Escalation(
        rule="knowledge_drift", severity="warning",
        message=f"Live FMEA lists tasks the World Model does not link to this fault: {listed}.",
        actions=["Treat the graph as the eligible work list", "Do not schedule a task that is only in the live table"],
        evidence_ids=evidence_ids,
    )


def data_quality(case: CaseFile, pack: dict) -> Escalation | None:
    found = serious_data_issues(case)
    if not found:
        return None
    details = "; ".join(dict.fromkeys(issue["detail"] for _, issue in found))
    return Escalation(
        rule="data_quality", severity="warning",
        message=f"Input data failed plausibility checks: {details[:400]}",
        actions=["Confirm the data with its owner before acting",
                 "Treat remaining-life and fault-mode numbers as unreliable until the data is confirmed"],
        evidence_ids=list(dict.fromkeys(i for i, _ in found)),
    )


def sb_adoption(case: CaseFile, pack: dict) -> list[Escalation]:
    """A recommended service bulletin that would replace a manual limit on a task in the plan: engineering decides;
    the plan keeps the manual meanwhile. Mandatory documents are hard constraints instead (document_mandate)."""
    out = []
    planned = set(case.recommendation.task_ids) if case.recommendation else set()
    for sb, manual in pending_adoptions(case):
        evidence = case.evidence.get(sb.evidence_id)
        if planned.isdisjoint(str((evidence.content.get("tasks") if evidence else "") or "").split(",")):
            continue
        topic = (evidence.content.get("topic") if evidence else None) or "the same topic"
        out.append(Escalation(
            rule="sb_adoption", severity="info",
            message=(f"{sb.doc_id} {sb.section} ({sb.doc_type.replace('_', ' ')}, compliance recommended) would replace "
                     f"{manual.doc_id} {manual.section} ({manual.doc_type.replace('_', ' ')}) on {topic}; SB "
                     "recommended; adopting it needs engineering approval."),
            actions=["Engineering to approve or decline adopting the service bulletin",
                     f"Until it is approved, the plan follows {manual.doc_id} {manual.section}"],
            evidence_ids=[sb.evidence_id, manual.evidence_id]))
    return out


def run_escalations(case: CaseFile, pack: dict) -> list[Escalation]:
    rest = (data_quality(case, pack), knowledge_drift(case, pack), rul_divergence(case, pack),
            ambiguous_fault(case, pack), rul_below_lead_time(case, pack))
    return [*document_mandates(case, pack), *[e for e in rest if e], *sb_adoption(case, pack)]
