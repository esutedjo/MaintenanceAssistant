"""Rule-check graph node. Wraps assurance.rules."""

from agents.runtime import NodeResult, RunContext, node
from assurance.rules import run_rule_checks
from casefile.schemas import MAX_ROUNDS, CaseFile


@node("rule_checks", "check", lambda s: f"claims={len(s.findings.claims)} rec={s.recommendation.action if s.recommendation else None}")
def rule_checks(state: CaseFile, ctx: RunContext) -> NodeResult:
    verdict = run_rule_checks(state, ctx.pack, ctx.settings.reference_time.date())
    update: dict = {"verdicts": [verdict]}
    if verdict.outcome == "revise" and state.round < MAX_ROUNDS:
        update["round"] = state.round + 1
    status = "success" if verdict.outcome == "accept" else "needs_revision"
    return NodeResult(update, f"{verdict.outcome}; {len(verdict.gaps)} gap(s)", status=status)
