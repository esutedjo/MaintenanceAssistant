"""Checkable challenges from the LLM critic. The model may only challenge in kinds that code can verify against the
evidence; a confirmed challenge sends the brief back, a refuted one is shown to the engineer as refuted. The model
never decides on its own and never supplies a number."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from casefile.schemas import CaseFile, Claim

KINDS = {
    "unsupported_number": "a claim states a number that none of its cited evidence records contains",
    "missing_mandate": "a mandatory document task is missing from the plan",
    "part_not_in_evidence": "a planned part number is not in the evidence it cites",
    "deadline_after_bound": "the deadline is later than the remaining-life lower bound allows",
    "wrong_engine": "the brief answers about a different engine than the question names",
}
# Identifiers carry digits that are not quantities: evidence IDs, part and task numbers, documents, dates, engines.
IDENTIFIER = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*-[\w.-]*|\b\d+(?:-\d+)+\b|\b\d+\.[A-Z]\b|\(\w\)")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
PAIRWISE_LIMIT = 400


def all_claims(case: CaseFile) -> list[Claim]:
    return [*case.findings.claims, *(case.recommendation.rationale if case.recommendation else [])]


def claim_numbers(text: str) -> list[tuple[float, float]]:
    """(value, tolerance): a whole number in the text was rounded, so it may sit 0.5 from the evidence."""
    return [(float(n), 0.051 if "." in n else 0.5) for n in NUMBER.findall(IDENTIFIER.sub(" ", text))]


def _evidence_numbers(case: CaseFile, ids: list[str], pack: dict | None = None) -> set[float]:
    """Numbers a claim may state from its cited evidence: the values themselves, the asset-pack thresholds, the
    difference or sum of two cited values (A minus B), and a cycles-to-days conversion of any of them."""
    found: set[float] = set()
    headline: set[float] = set()          # what a record states up front: its summary and top-level values
    for eid in ids:
        item = case.evidence.get(eid)
        if item is not None:
            found |= {float(n) for n in NUMBER.findall(f"{item.summary} {json.dumps(item.content, default=str)}")}
            headline |= {float(n) for n in NUMBER.findall(item.summary)}
            headline |= {float(v) for v in (item.content or {}).values()
                         if isinstance(v, (int, float)) and not isinstance(v, bool)}
    values = sorted(headline)[:PAIRWISE_LIMIT]
    found |= {abs(x - y) for x in values for y in values} | {x + y for x in values for y in values}
    thresholds = (pack or {}).get("thresholds") or {}
    found |= {float(v) for v in thresholds.values() if isinstance(v, (int, float))}
    per_day = float(thresholds.get("cycles_per_day") or 0)
    if per_day:
        found |= {x / per_day for x in values} | {x * per_day for x in values}
    return found


def _close(value: float, candidates: set[float], tolerance: float = 0.051) -> bool:
    return any(abs(value - c) <= max(tolerance, 0.005 * abs(c)) for c in candidates)


def unsupported_numbers(case: CaseFile, claim: Claim, pack: dict | None = None) -> list[float]:
    """Numbers a claim states that its cited evidence does not support."""
    present = _evidence_numbers(case, claim.evidence_ids, pack)
    return [n for n, tol in claim_numbers(claim.text) if not _close(n, present, tol)]


def verify(case: CaseFile, pack: dict, reference: date, challenge: dict[str, Any]) -> tuple[bool, str]:
    """(confirmed, reason). Only the kinds in KINDS are checked; anything else is refuted as not checkable."""
    kind = challenge.get("kind")
    rec = case.recommendation
    if kind == "unsupported_number":
        claims = all_claims(case)
        try:
            index, value = int(challenge.get("claim")), float(challenge.get("value"))
        except (TypeError, ValueError):
            return False, "the challenge names no claim number and value"
        if not 0 <= index < len(claims):
            return False, f"there is no claim [{index}]"
        claim = claims[index]
        stated = [(n, tol) for n, tol in claim_numbers(claim.text) if abs(n - value) <= 0.051]
        if not stated:
            return False, f"claim [{index}] does not state {value:g}"
        if _close(value, _evidence_numbers(case, claim.evidence_ids, pack), stated[0][1]):
            return False, f"{', '.join(claim.evidence_ids)} supports {value:g}"
        return True, f"claim [{index}] states {value:g}, which none of {', '.join(claim.evidence_ids) or 'its'} evidence contains"
    if kind == "missing_mandate":
        missing = [d.required_task for d in case.findings.documents
                   if d.mandatory and d.required_task and (rec is None or d.required_task not in rec.task_ids)]
        return (True, f"mandated task {', '.join(missing)} is not in the plan") if missing else \
            (False, "every mandated task is in the plan")
    if kind == "part_not_in_evidence":
        if rec is None:
            return False, "there is no plan"
        absent = [p.part_number for p in rec.parts
                  if not any(p.part_number in json.dumps(case.evidence[i].content, default=str)
                             for i in p.evidence_ids if i in case.evidence)]
        return (True, f"part {', '.join(absent)} is not in the evidence it cites") if absent else \
            (False, "every part is in the evidence it cites")
    if kind == "deadline_after_bound":
        a, b = case.findings.rul_ml, case.findings.rul_trend
        if rec is None or rec.deadline is None or a is None:
            return False, "there is no deadline or remaining-life estimate"
        lower = min(a.lower, b.lower if b else a.lower)
        latest = reference + timedelta(days=int(lower / pack["thresholds"]["cycles_per_day"]))
        return (True, f"deadline {rec.deadline} is after {latest}, the bound of {lower:.0f} cycles") \
            if rec.deadline > latest else (False, f"deadline {rec.deadline} is within the bound ({latest})")
    if kind == "wrong_engine":
        asked, answered = case.intent.asset_id if case.intent else None, case.asset.asset_id if case.asset else None
        return (True, f"the question names {asked}, the brief is about {answered}") \
            if asked and answered and asked != answered else (False, f"the brief is about {answered}, as asked")
    return False, f"{kind!r} is not a challenge code can check"


def prompt_section(case: CaseFile) -> str:
    """Numbered claims and the challenge format, appended to the critic prompt."""
    claims = "\n".join(f"[{i}] {c.text} ({', '.join(c.evidence_ids)})" for i, c in enumerate(all_claims(case)))
    kinds = "\n".join(f"- {k}: {v}" for k, v in KINDS.items())
    return ("Numbered claims (brief and plan):\n" + claims + "\n"
            "Also return \"challenges\": a list of checkable objections, each "
            "{\"kind\": one of the kinds below, \"claim\": claim number or null, \"value\": the disputed number or "
            "null, \"detail\": one short sentence}. Code verifies each one against the evidence; only a confirmed "
            "challenge sends the brief back. Use [] when you have none.\nKinds:\n" + kinds + "\n")
