"""Deterministic rule checks. Run after the maintenance planner, before the critic. No LLM."""

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
        for evidence_id in part.evidence_ids:
            evidence = case.evidence.get(evidence_id)
            if evidence and part.part_number in (evidence.content.get("unknown") or []):
                gaps.append(f"part {part.part_number} is not in the catalogue")
    return gaps


def slot_exists(case: CaseFile) -> list[str]:
    rec = case.recommendation
    if rec is None or rec.slot is None:
        return []
    for evidence in case.evidence.values():
        nxt = evidence.content.get("next") or []
        first = evidence.content.get("first_free")
        dates = {first, *nxt} - {None}
        if rec.slot.isoformat() in dates:
            return []
        if dates:
            return [f"slot {rec.slot} is not in the free-slot list"]
    return ["recommendation has a slot but no free-slot evidence"]


def run_rule_checks(case: CaseFile, pack: dict) -> Verdict:
    cap = float(pack["thresholds"]["rul_cap_cycles"])
    gaps = [*claims_have_evidence(case), *rul_in_range(case, cap), *asset_matches(case),
            *parts_exist(case), *slot_exists(case)]
    return Verdict(round=case.round, gate="rule_checks", outcome="accept" if not gaps else "revise", gaps=gaps)
