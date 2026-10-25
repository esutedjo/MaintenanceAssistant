"""Deterministic escalation rules. Thresholds come from the asset pack. Never writes the recommendation."""

from casefile.schemas import CaseFile, Escalation


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
    for evidence in case.evidence.values():
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


def run_escalations(case: CaseFile, pack: dict) -> list[Escalation]:
    rest = (data_quality(case, pack), knowledge_drift(case, pack), rul_divergence(case, pack),
            ambiguous_fault(case, pack), rul_below_lead_time(case, pack))
    return [*document_mandates(case, pack), *[e for e in rest if e]]
