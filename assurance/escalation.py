"""Deterministic escalation rules. Thresholds come from the asset pack. Never writes the recommendation."""

from datetime import date, timedelta

from casefile.schemas import RESULT_RANK, CaseFile, DocumentFinding, Escalation, InspectionAssessment

# Document types whose compliance is recommended unless a mandate in them fires. One that wins a topic over a manual
# by precedence is not adopted silently: the plan keeps the manual and an escalation asks engineering to decide.
RECOMMENDED_TYPES = {"service_bulletin"}
RESULT_WORDS = {"within": "within limits", "at_limit": "at the limit", "beyond": "beyond limits"}


def needs_approval(doc: DocumentFinding) -> bool:
    """A recommended bulletin engineering has not yet adopted or declined for the fleet (engineering order)."""
    return doc.doc_type in RECOMMENDED_TYPES and not doc.mandatory and doc.fleet_decision is None


def needs_acknowledgement(severity: str | None, rule: str | None = None) -> bool:
    """Urgent, warning and mandatory-document escalations need a tick before approval; an info escalation is a note
    the sign-off records as seen, so approval is not buried in ticks for advice that changes nothing."""
    return severity != "info" or rule == "document_mandate"


def pending_adoptions(case: CaseFile) -> list[tuple[DocumentFinding, DocumentFinding]]:
    """(recommended document, the document it would replace) for each topic a non-mandatory SB wins by precedence."""
    by_label = {f"{d.doc_id} {d.section}": d for d in case.findings.documents}
    return [(by_label[d.overridden_by], d) for d in case.findings.documents
            if d.overridden_by in by_label and needs_approval(by_label[d.overridden_by])]


def document_tasks(case: CaseFile, doc: DocumentFinding) -> set[str]:
    evidence = case.evidence.get(doc.evidence_id)
    return {t for t in str((evidence.content.get("tasks") if evidence else "") or "").split(",") if t}


def manual_for_task(case: CaseFile, task_id: str) -> DocumentFinding | None:
    """The applicable manual section that covers a task. A bulletin that names the task but shares no topic with a
    manual section replaces nothing by precedence; until engineering adopts it, the work follows this section."""
    return next((d for d in case.findings.documents if d.doc_type == "manual" and task_id in document_tasks(case, d)),
                None)


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


def answered_by(case: CaseFile, pack: dict, modes: list[str]) -> InspectionAssessment | None:
    """A current, fresh inspection finding on a module one of these fault modes wears: the inspection a disagreement
    or an unclear fault asks for has been done, so the caution is answered (the finding's own rule then decides)."""
    modules = {pack["failure_modes"][m]["component"] for m in modes if m in pack.get("failure_modes", {})}
    found = [i for i in case.findings.inspections if i.current and i.fresh and i.module in modules]
    return max(found, key=lambda i: RESULT_RANK[i.result]) if found else None


def rul_divergence(case: CaseFile, pack: dict) -> Escalation | None:
    a, b = case.findings.rul_ml, case.findings.rul_trend
    if a is None or b is None:
        return None
    gap = abs(a.rul - b.rul)
    limit = pack["thresholds"]["rul_divergence_cycles"]
    if gap <= limit:
        return None
    message = f"RUL A ({a.rul:.1f}) and RUL B ({b.rul:.1f}) differ by {gap:.1f} cycles (limit {limit:.0f})."
    answer = answered_by(case, pack, [m.failure_mode for m in case.findings.fault_modes[:1]])
    if answer is not None:
        return Escalation(
            rule="rul_divergence", severity="info",
            message=f"{message[:-1]}; the fresh inspection finding {answer.evidence_id} "
                    f"({RESULT_WORDS[answer.result]}) answers it.",
            actions=["Inspection done: the plan follows the finding", "Do not average the two remaining-life estimates"],
            evidence_ids=[a.evidence_id, b.evidence_id, answer.evidence_id],
        )
    return Escalation(
        rule="rul_divergence", severity="warning", message=message,
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
    message = f"{labels[0]} ({modes[0].probability:.2f}) and {labels[1]} ({modes[1].probability:.2f}) are within {limit}."
    answer = answered_by(case, pack, [m.failure_mode for m in modes[:2]])
    return Escalation(
        rule="ambiguous_fault", severity="info" if answer else "warning",
        message=message if answer is None else (f"{message[:-1]}; the fresh inspection finding {answer.evidence_id} "
                                                f"({RESULT_WORDS[answer.result]}) answers it."),
        actions=["Inspection done: the plan follows the finding"] if answer else ["Inspection targeting both modules"],
        evidence_ids=[modes[0].evidence_id, modes[1].evidence_id, *([answer.evidence_id] if answer else [])],
    )


def inspection_findings(case: CaseFile, pack: dict) -> list[Escalation]:
    """A current finding beyond limits is urgent (remove before further flight); at the limit, a warning to re-inspect
    within the manual's interval. Within limits raises nothing."""
    out = []
    for item in [i for i in case.findings.inspections if i.current and i.result != "within"]:
        evidence = case.evidence.get(item.evidence_id)
        finding = evidence.content.get("finding", {}) if evidence else {}
        measured = finding.get("measurement")
        state = "beyond the limits" if item.result == "beyond" else "at the limit"
        if item.classified_by == "limit" and measured:
            detail = f"{measured['parameter']} {measured['value']:g} {measured['unit']} is {state} of {item.reference}"
        else:
            detail = f"{state} as classified by the inspector {finding.get('inspector') or ''} (no limit applies)"
        head = f"Inspection finding {item.evidence_id} ({item.kind.replace('_', ' ')}, {item.location or item.module}, "
        cited = [item.evidence_id, *([item.limit_evidence_id] if item.limit_evidence_id else [])]
        if item.result == "beyond":
            out.append(Escalation(
                rule="inspection_beyond_limits", severity="urgent", message=f"{head}cycle {item.cycle}): {detail}.",
                actions=["Remove the engine before further flight",
                         "Mandatory: the plan cannot propose less than the manual requires"], evidence_ids=cited))
        else:
            out.append(Escalation(
                rule="inspection_at_limit", severity="warning",
                message=f"{head}cycle {item.cycle}): {detail}; re-inspect within {item.interval_cycles} cycles.",
                actions=[f"Re-inspect within {item.interval_cycles} cycles", "Plan the work the manual names"],
                evidence_ids=cited))
    return out


REPORTABLE = ("Potentially reportable: check your occurrence reporting scheme (EU 376/2014 and 2015/1018 Annex II; "
              "14 CFR 121.703 / 145.221). The decision to report stays with people.")


def mandate_missed(case: CaseFile, pack: dict, reference: date | None) -> list[str]:
    """Mandatory document actions the plan would complete late: its deadline or first hangar slot after the compliance
    date, or a part of the mandated task short with a lead time that ends after it."""
    rec = case.recommendation
    if rec is None or reference is None:
        return []
    task_parts = {t["fmea_id"]: {p.get("part_number") for p in t.get("parts") or []}
                  for e in case.evidence.values() if ":world/" in (e.source or "") for t in e.content.get("tasks") or []}
    missed = []
    for doc in [d for d in case.findings.documents if d.mandatory and d.within_cycles is not None]:
        due = reference + timedelta(days=int(doc.within_cycles / pack["thresholds"]["cycles_per_day"]))
        late = []
        if rec.deadline and rec.deadline > due:
            late.append(f"deadline {rec.deadline}")
        if rec.slot and rec.slot > due:
            late.append(f"first hangar slot {rec.slot}")
        late += [f"part {p.part_number} short with {p.lead_time_days} d lead time" for p in rec.parts
                 if p.part_number in task_parts.get(doc.required_task or "", set()) and p.available < p.qty
                 and reference + timedelta(days=p.lead_time_days) > due]
        if late:
            missed.append(f"{doc.doc_id} {doc.section} due {due} ({', '.join(late)})")
    return missed


def potentially_reportable(case: CaseFile, pack: dict, reference: date | None = None) -> Escalation | None:
    """A note, not a filing: a finding beyond limits, or a mandatory action the plan would complete late, may be an
    occurrence to report. Cleris files nothing; people decide."""
    beyond = [i for i in case.findings.inspections if i.current and i.result == "beyond"]
    missed = mandate_missed(case, pack, reference)
    if not beyond and not missed:
        return None
    why = [f"inspection finding {i.evidence_id} is beyond limits" for i in beyond] + \
          [f"mandatory action {m} would be missed" for m in missed]
    docs = [d.evidence_id for d in case.findings.documents if d.mandatory] if missed else []
    return Escalation(
        rule="potentially_reportable", severity="info", message=f"{REPORTABLE} Why: {'; '.join(why)}.",
        actions=["Check the occurrence reporting scheme",
                 "Cleris files nothing; the decision to report stays with people"],
        evidence_ids=[*(i.evidence_id for i in beyond), *docs] or [case.findings.rul_ml.evidence_id])


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
    short = any(p.available < p.qty for p in rec.parts)
    wait = (f"lead time of short parts {lead} d plus next free slot {slot_days} d" if short
            else f"all parts on hand; next free slot {slot_days} d")
    grounded = days <= 0
    # Expedite only helps a short part; derate only stretches remaining life there is none of at a bound of 0.
    actions = [*(["Propose expedite"] if short else []), "Propose engine swap",
               *([] if grounded else ["Propose derate"])]
    return Escalation(
        rule="rul_below_lead_time", severity="urgent",
        message=(f"RUL lower bound is {days:.1f} days" + (" (do not dispatch)" if grounded else "") + f"; {wait}."),
        actions=actions,
        evidence_ids=list(dict.fromkeys([*evidence_ids, *extra]))[:8] or evidence_ids,
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
    the plan keeps the manual meanwhile. Mandatory documents are hard constraints instead (document_mandate). Once
    engineering records a fleet decision (engineering order), the brief cites it and this note no longer fires."""
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
    # A recommended bulletin that shares no topic with a manual section replaces nothing by precedence, but it is
    # still not approved data for the fleet: the work follows the manual section that covers the task meanwhile.
    paired = {sb.evidence_id for sb, _ in pending_adoptions(case)}
    for sb in case.findings.documents:
        evidence = case.evidence.get(sb.evidence_id)
        tasks = sorted(document_tasks(case, sb) & planned)
        if not needs_approval(sb) or sb.evidence_id in paired or not (evidence and evidence.content.get("topic")) \
                or not tasks:
            continue
        manual = next((m for t in tasks if (m := manual_for_task(case, t))), None)
        follows = (f"the plan follows {manual.doc_id} {manual.section} for {', '.join(tasks)}" if manual else
                   f"no approved document sets the limits and intervals for {', '.join(tasks)}")
        out.append(Escalation(
            rule="sb_adoption", severity="info",
            message=(f"{sb.doc_id} {sb.section} ({sb.doc_type.replace('_', ' ')}, compliance recommended) covers "
                     f"{', '.join(tasks)} in the plan ({evidence.content['topic']}); SB recommended; adopting it needs "
                     "engineering approval."),
            actions=["Engineering to approve or decline adopting the service bulletin",
                     f"Until it is approved, {follows}"],
            evidence_ids=[sb.evidence_id, *([manual.evidence_id] if manual else [])]))
    return out


def run_escalations(case: CaseFile, pack: dict, reference: date | None = None) -> list[Escalation]:
    rest = (data_quality(case, pack), knowledge_drift(case, pack), rul_divergence(case, pack),
            ambiguous_fault(case, pack), rul_below_lead_time(case, pack))
    reportable = potentially_reportable(case, pack, reference)
    return [*document_mandates(case, pack), *inspection_findings(case, pack), *[e for e in rest if e],
            *sb_adoption(case, pack), *([reportable] if reportable else [])]
