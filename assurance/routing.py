"""Gap kinds and the agent that can fix each. Every gap that blocks a brief (rule checks, the critic's deterministic
checks, a Nemotron challenge code confirmed) gets a kind; the planner hands each kind to its owner, and the fixed
brief goes back through the rule checks and the critic. Gates only name the gap; they never write the fix."""

import re

from casefile.schemas import GapRoute

# kind: (agent that can fix it, what that agent does). None: no agent can, so the case closes for the engineer.
ROUTES: dict[str, tuple[str | None, str]] = {
    "missing_mandate": ("knowledge", "re-read the mandatory document and bind the task it requires for the plan"),
    "uncited_mandate": ("knowledge", "re-read the mandatory document and bind its citation for the plan"),
    "mandate_deadline": ("knowledge", "re-read the mandatory document and bind its compliance date for the plan"),
    "part_not_in_evidence": ("data", "re-query stock and lead time for the named parts, skipping the run's cache"),
    "stock_mismatch": ("data", "re-query stock and lead time for the named parts, skipping the run's cache"),
    "slot_not_free": ("data", "re-query the free hangar slots, skipping the run's cache"),
    "similar_cases_missing": ("diagnostics", "search case memory again, without the fault-mode tag"),
    "rul_mismatch": ("prognostics", "recompute both remaining-life estimates from the models"),
    "unsupported_number": ("maint_planner", "rebuild the plan's claims and withdraw any number its evidence lacks"),
    "deadline_after_bound": ("maint_planner", "reset the deadline to the remaining-life lower bound"),
    "driver_signature": ("maint_planner", "name the sensors that distinguish the fault mode"),
    "uncited_claim": ("maint_planner", "rebuild the plan's claims from evidence that resolves"),
    "task_not_eligible": ("maint_planner", "keep only tasks the World Model lists for the fault"),
    "inspection_not_cited": ("maint_planner", "rebuild the plan citing each current inspection finding"),
    "inspection_not_acted_on": ("maint_planner", "rebuild the plan on the inspection result the manual's limit gives"),
    "inspection_result_mismatch": (None, "no agent may reclassify an inspection finding mid-loop; the engineer checks "
                                         "the finding against its limit"),
    "fault_mode_mismatch": (None, "no agent may rewrite a fault-mode finding mid-loop"),
    "wrong_engine": (None, "the brief is about another engine than the question names; ask again"),
    "other": ("maint_planner", "rebuild the plan"),
}

# Gap wording comes from assurance/rules.py, agents/critic.py and checked_challenges (kind name first); the order
# matters where two patterns overlap (the RUL-bound deadline before the document deadline).
PATTERNS: list[tuple[re.Pattern, str]] = [(re.compile(p), k) for p, k in [
    (r"^claim cites unknown evidence", "uncited_claim"),
    (r"^RUL \w+ .*(outside 0\.\.|does not contain|evidence .* is missing|differs from)", "rul_mismatch"),
    (r"^fault mode .* differs from", "fault_mode_mismatch"),
    (r"^intent asset .* does not match|^wrong engine: ", "wrong_engine"),
    (r"^part \S+ (cites unknown evidence|is not in the catalogue|is not listed in the evidence)|^part not in evidence: ",
     "part_not_in_evidence"),
    (r"^part \S+ (has negative availability|: .* differs from stock evidence)|^part \S+: .* differs from stock",
     "stock_mismatch"),
    (r"^slot .* is not in the free-slot list|^recommendation has a slot but no free-slot evidence", "slot_not_free"),
    (r"^mandatory task .* is missing from the plan|^missing mandate: ", "missing_mandate"),
    (r"^recommendation does not cite mandatory document", "uncited_mandate"),
    (r"^deadline .* is later than the RUL lower bound allows|^deadline after bound: ", "deadline_after_bound"),
    (r"^deadline .* is later than .* allows", "mandate_deadline"),
    (r"^task .* is not on the World Model", "task_not_eligible"),
    (r"^claim states .* which its cited evidence|^unsupported number: ", "unsupported_number"),
    (r"^RUL drivers listed are", "driver_signature"),
    (r"^inspection finding \S+ \(.*?\) is not cited by the plan", "inspection_not_cited"),
    (r"^inspection finding \S+ \(.*?\) is (beyond limits|at the limit) but", "inspection_not_acted_on"),
    (r"^inspection finding \S+ \(.*?\) (is reported|has no evidence record)", "inspection_result_mismatch"),
    (r"^Degradation is detected but no similar past cases", "similar_cases_missing"),
]]


def gap_kind(gap: str) -> str:
    return next((kind for pattern, kind in PATTERNS if pattern.search(gap)), "other")


def routes_for(gaps: list[str]) -> list[GapRoute]:
    return [GapRoute(kind=kind, target=ROUTES[kind][0], gap=gap) for gap in gaps for kind in [gap_kind(gap)]]


PART_NUMBER = re.compile(r"\b[A-Z]{2}-[A-Z]{2,4}-\d{3,}\b")     # TF-HPC-1100; not EV-MNT-03 or FM-HPC-01


def named_parts(gaps: list[str]) -> list[str]:
    """Part numbers a parts gap names, for a targeted re-query."""
    return list(dict.fromkeys(m for gap in gaps for m in PART_NUMBER.findall(gap)))
