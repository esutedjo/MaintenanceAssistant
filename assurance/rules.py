"""Deterministic rule checks. Run after the maintenance planner, before the critic. No LLM."""

from datetime import date, timedelta

from assurance.routing import routes_for
from casefile.schemas import CaseFile, Verdict


def claims_have_evidence(case: CaseFile) -> list[str]:
    claims = [*case.findings.claims, *(case.recommendation.rationale if case.recommendation else [])]
    gaps = []
    for claim in claims:
        missing = case.unresolved(claim.evidence_ids)
        if missing:
            gaps.append(f"claim cites unknown evidence {missing}: {claim.text[:120]}")
    return gaps


def rul_in_range(case: CaseFile, cap: float) -> list[str]:
    gaps = []
    for finding in (case.findings.rul_ml, case.findings.rul_trend):
        if finding is None:
            continue
        for name, value in (("rul", finding.rul), ("lower", finding.lower), ("upper", finding.upper)):
            if not 0 <= value <= cap:
                gaps.append(f"RUL {finding.method} {name}={value} is outside 0..{cap}")
        if not finding.lower <= finding.rul <= finding.upper:
            gaps.append(f"RUL {finding.method} interval {finding.lower}..{finding.upper} does not contain {finding.rul}")
        if case.unresolved([finding.evidence_id]):
            gaps.append(f"RUL {finding.method} evidence {finding.evidence_id} is missing")
    return gaps


def asset_matches(case: CaseFile) -> list[str]:
    if case.asset is None or case.intent is None or case.intent.asset_id is None:
        return []
    if case.intent.asset_id != case.asset.asset_id:
        return [f"intent asset {case.intent.asset_id} does not match resolved {case.asset.asset_id}"]
    return []


def parts_exist(case: CaseFile) -> list[str]:
    if case.recommendation is None:
        return []
    gaps = []
    for part in case.recommendation.parts:
        if case.unresolved(part.evidence_ids):
            gaps.append(f"part {part.part_number} cites unknown evidence {part.evidence_ids}")
        if part.available < 0:
            gaps.append(f"part {part.part_number} has negative availability {part.available}")
        cited = [case.evidence[i] for i in part.evidence_ids if i in case.evidence]
        if any(part.part_number in (e.content.get("unknown") or []) for e in cited):
            gaps.append(f"part {part.part_number} is not in the catalogue")
            continue
        stock = [row for e in cited for row in e.content.get("parts") or [] if row.get("part_number") == part.part_number]
        listed = stock or [row for e in cited for task in e.content.get("tasks") or []
                           for row in task.get("parts") or [] if row.get("part_number") == part.part_number]
        if not listed:
            gaps.append(f"part {part.part_number} is not listed in the evidence it cites {part.evidence_ids}")
        elif stock and (part.available != stock[0].get("available")
                        or part.lead_time_days != (stock[0].get("lead_time_days") or 0)):
            gaps.append(f"part {part.part_number}: {part.available} available, {part.lead_time_days} d lead time "
                        f"differs from stock evidence ({stock[0].get('available')}, {stock[0].get('lead_time_days')} d)")
    return gaps


def findings_match_evidence(case: CaseFile, tolerance: float = 0.05) -> list[str]:
    """Reported numbers must equal the model output they cite; a brief may not round an engine healthier."""
    gaps = []
    for finding in (case.findings.rul_ml, case.findings.rul_trend):
        evidence = case.evidence.get(finding.evidence_id) if finding else None
        if evidence is None:
            continue
        for name in ("rul", "lower", "upper"):
            source = evidence.content.get(name)
            if source is not None and abs(getattr(finding, name) - float(source)) > tolerance:
                gaps.append(f"RUL {finding.method} {name}={getattr(finding, name)} differs from "
                            f"{finding.evidence_id} ({source})")
    for mode in case.findings.fault_modes:
        evidence = case.evidence.get(mode.evidence_id)
        source = (evidence.content.get("probabilities") or {}).get(mode.failure_mode) if evidence else None
        if source is not None and abs(mode.probability - float(source)) > 0.01:
            gaps.append(f"fault mode {mode.failure_mode} p={mode.probability} differs from {mode.evidence_id} ({source})")
    return gaps


def slot_exists(case: CaseFile) -> list[str]:
    rec = case.recommendation
    if rec is None or rec.slot is None:
        return []
    listed = False                       # a re-queried free-slot list counts as well as the first one
    for evidence in case.evidence.values():
        nxt = evidence.content.get("next") or []
        first = evidence.content.get("first_free")
        dates = {first, *nxt} - {None}
        if rec.slot.isoformat() in dates:
            return []
        listed |= bool(dates)
    return [f"slot {rec.slot} is not in the free-slot list" if listed
            else "recommendation has a slot but no free-slot evidence"]


def mandates_honoured(case: CaseFile, pack: dict, reference: date | None = None) -> list[str]:
    """A mandatory document action may not be dropped, cited away or pushed past its compliance time."""
    mandates = [d for d in case.findings.documents if d.mandatory]
    rec = case.recommendation
    if not mandates or rec is None:
        return []
    gaps = []
    cited = {i for c in rec.rationale for i in c.evidence_ids}
    for doc in mandates:
        label = f"{doc.doc_id} {doc.section}"
        if doc.required_task and doc.required_task not in rec.task_ids:
            gaps.append(f"mandatory task {doc.required_task} from {label} is missing from the plan")
        if doc.evidence_id not in cited:
            gaps.append(f"recommendation does not cite mandatory document {label} ({doc.evidence_id})")
        if reference and doc.within_cycles is not None and rec.deadline:
            limit = reference + timedelta(days=int(doc.within_cycles / pack["thresholds"]["cycles_per_day"]))
            if rec.deadline > limit:
                gaps.append(f"deadline {rec.deadline} is later than {label} allows ({limit})")
    return gaps


def world_tasks_eligible(case: CaseFile) -> list[str]:
    """A proposed task must sit on a World Model addresses edge for the claimed fault."""
    rec = case.recommendation
    if rec is None or not rec.task_ids:
        return []
    allowed: list[str] = []
    world = [e for e in case.evidence.values()
             if "task_ids" in e.content and "world/" in (e.source or "")]
    if not world:
        return []
    for evidence in world:
        allowed.extend(evidence.content.get("task_ids") or [])
    return [f"task {tid} is not on the World Model addresses edge for this fault" for tid in rec.task_ids
            if tid not in set(allowed)]


def deadline_within_rul(case: CaseFile, pack: dict, reference: date | None = None) -> list[str]:
    """The deadline may be earlier than the RUL lower bound (a document can tighten it), never later."""
    rec, a, b = case.recommendation, case.findings.rul_ml, case.findings.rul_trend
    if reference is None or rec is None or rec.deadline is None or a is None:
        return []
    lower = min(a.lower, b.lower if b else a.lower)
    limit = reference + timedelta(days=int(lower / pack["thresholds"]["cycles_per_day"]))
    if rec.deadline > limit:
        return [f"deadline {rec.deadline} is later than the RUL lower bound allows ({limit}, {lower:.0f} cycles)"]
    return []


REMOVAL_TYPES = {"replace", "engine_change"}


def _removes(case: CaseFile) -> bool:
    """The plan takes the engine off: a replace or engine-change task the World Model lists, or the removal action."""
    rec = case.recommendation
    types = {t.get("fmea_id"): t.get("task_type") for e in case.evidence.values()
             if ":world/" in (e.source or "") for t in e.content.get("tasks") or []}
    return (any(types.get(t) in REMOVAL_TYPES for t in rec.task_ids)
            or "remove the engine before further flight" in rec.action.lower())


def inspections_acted_on(case: CaseFile, pack: dict, reference: date | None = None) -> list[str]:
    """After an inspection the plan must cite each current finding, the result must be what the measurement and the
    cited limit say (code reclassifies it), and the plan may never do less than the manual requires: beyond limits,
    removal before further flight; at the limit, a deadline within the re-inspection interval."""
    from tools.inspections import classify, find_row

    rec = case.recommendation
    current = [i for i in case.findings.inspections if i.current]
    if rec is None or not current:
        return []
    cited = {i for c in rec.rationale for i in c.evidence_ids}
    gaps = []
    for item in current:
        label = f"inspection finding {item.evidence_id} ({item.result.replace('_', ' ')})"
        record, limit = case.evidence.get(item.evidence_id), case.evidence.get(item.limit_evidence_id or "")
        if record is None or limit is None:
            gaps.append(f"{label} has no evidence record for the finding or its limit")
            continue
        measured = record.content.get("finding", {}).get("measurement")
        row = find_row(limit.content.get("limits") or [], (item.limit or {}).get("id") or "") if item.limit else None
        if item.classified_by == "limit" and (row is None or measured is None):
            gaps.append(f"{label} is reported as compared with a limit that {item.limit_evidence_id} does not list")
        elif item.classified_by == "limit" and classify(float(measured["value"]), row) != item.result:
            gaps.append(f"{label} is reported {item.result.replace('_', ' ')} but its measurement "
                        f"{measured['value']:g} {measured['unit']} against {item.limit_evidence_id} is "
                        f"{classify(float(measured['value']), row).replace('_', ' ')}")
        if item.evidence_id not in cited:
            gaps.append(f"{label} is not cited by the plan")
        if item.result == "beyond":
            if reference and rec.deadline and rec.deadline > reference:
                gaps.append(f"{label} is beyond limits but the deadline {rec.deadline} is after {reference}: the "
                            "manual requires removal before further flight")
            if not _removes(case):
                gaps.append(f"{label} is beyond limits but the plan does not remove the engine")
        elif item.result == "at_limit" and reference and rec.deadline and item.interval_cycles:
            due = reference + timedelta(days=int(item.interval_cycles / pack["thresholds"]["cycles_per_day"]))
            if rec.deadline > due:
                gaps.append(f"{label} is at the limit but the deadline {rec.deadline} is later than its re-inspection "
                            f"interval allows ({due}, {item.interval_cycles} cycles)")
    return gaps


def numbers_supported(case: CaseFile, pack: dict) -> list[str]:
    """Every number a claim states must be in its cited evidence, a pack threshold, a sum or difference of two
    cited values, or a cycles-to-days conversion. 0 of 2,907 clean claims fail this on the fleet (25 Oct)."""
    from assurance.challenges import all_claims, unsupported_numbers
    gaps = []
    for claim in all_claims(case):
        if claim.evidence_ids and not case.unresolved(claim.evidence_ids):
            bad = unsupported_numbers(case, claim, pack)
            if bad:
                gaps.append(f"claim states {', '.join(f'{n:g}' for n in bad)}, which its cited evidence "
                            f"({', '.join(claim.evidence_ids)}) does not support: {claim.text[:120]}")
    return gaps


def run_rule_checks(case: CaseFile, pack: dict, reference: date | None = None) -> Verdict:
    cap = float(pack["thresholds"]["rul_cap_cycles"])
    gaps = [*claims_have_evidence(case), *rul_in_range(case, cap), *findings_match_evidence(case), *asset_matches(case),
            *parts_exist(case), *slot_exists(case), *mandates_honoured(case, pack, reference),
            *deadline_within_rul(case, pack, reference), *world_tasks_eligible(case),
            *inspections_acted_on(case, pack, reference), *numbers_supported(case, pack)]
    return Verdict(round=case.round, gate="rule_checks", outcome="accept" if not gaps else "revise", gaps=gaps,
                   routes=routes_for(gaps))
