"""Deterministic escalation rules. Thresholds come from the asset pack. Never writes the recommendation."""

from casefile.schemas import CaseFile, Escalation


def remaining_shorter_than_wait(rul_days: float, lead_days: float, slot_days: float) -> bool:
    """True when the RUL lower bound (days) cannot cover parts lead time plus days to the next free slot."""
    return rul_days < lead_days + slot_days


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
        message=f"RUL A ({a.rul:.0f}) and RUL B ({b.rul:.0f}) differ by {gap:.0f} cycles (limit {limit:.0f}).",
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
    lead = max((p.lead_time_days for p in rec.parts), default=0)
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
        message=(f"RUL lower bound is {days:.1f} days; parts lead time {lead} d plus next free slot {slot_days} d."),
        actions=["Propose expedite", "Propose engine swap", "Propose derate"],
        evidence_ids=[*evidence_ids, *extra][:8] or evidence_ids,
    )


def run_escalations(case: CaseFile, pack: dict) -> list[Escalation]:
    return [e for e in (rul_divergence(case, pack), ambiguous_fault(case, pack), rul_below_lead_time(case, pack)) if e]
