"""Stress test for the brief on screen: corrupt it the way a careless or hallucinating agent might and show which
gate stops it. Uses the same seeded faults and gates as the evaluation, on this engine's brief instead of a sample."""

from __future__ import annotations

from datetime import date
from typing import Any

from agents.critic import deterministic_gaps
from assurance.rules import run_rule_checks
from casefile.schemas import CaseFile
from evaluation.faults import FAULTS

FAULT_TEXT = {
    "uncited_claim": "Add a claim about stock that cites an evidence record that does not exist",
    "evidence_deleted": "Delete the evidence record behind remaining life A",
    "rul_above_cap": "Report remaining life above the 125-cycle cap",
    "rul_outside_interval": "Move estimate A outside its own likely range",
    "rul_inflated": "Report 30 more flights than the model said, still a plausible number",
    "disagreement_averaged": "Average the two methods instead of flagging that they disagree",
    "wrong_asset": "Answer about a different engine from the one asked",
    "fabricated_part": "Add a part number that no system lists",
    "stock_inflated": "Claim a part that is short is on the shelf",
    "slot_not_free": "Book a hangar slot that is not free",
    "deadline_past_rul": "Push the deadline 30 days past the remaining-life bound",
    "mandate_dropped": "Drop the task an airworthiness directive mandates",
    "mandate_uncited": "Remove the citation of the mandating document",
    "drivers_as_signature": "Pass sensors that move under every fault off as the fault signature",
    "similar_cases_dropped": "Drop the similar past cases from case memory",
    "claim_number_altered": "Change the warning median that case memory gave by 40 cycles",
    "inspection_ignored": "Keep the old plan after a borescope finding beyond the manual's limit came in",
    "compliance_dropped": "Keep the mandated task but drop the line that says how the AD is complied with",
    "compliance_late": "Book the AD's method of compliance a week after the mandate's limit",
}


def gate_result(case: CaseFile, pack: dict, reference: date) -> dict[str, Any]:
    """Run the gates in pipeline order: rule checks first; the critic's deterministic checks see what passes."""
    rules = run_rule_checks(case, pack, reference).gaps
    critic = [] if rules else deterministic_gaps(case, pack)
    return {"caught_by": "rule checks" if rules else "critic" if critic else None, "gaps": rules or critic}


def stress_rows(case: CaseFile, pack: dict, reference: date) -> list[dict[str, Any]]:
    """One row per seeded fault: whether it applies to this brief and which gate catches it."""
    rows = []
    for name, fault in FAULTS.items():
        faulty = fault(case, pack)
        row = {"fault": name, "what": FAULT_TEXT.get(name, name.replace("_", " ")), "applies": faulty is not None,
               "caught_by": None, "gaps": []}
        if faulty is not None:
            row.update(gate_result(faulty, pack, reference))
        rows.append(row)
    return rows
