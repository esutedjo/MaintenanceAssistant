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
    summary = f"{verdict.outcome}; {len(verdict.gaps)} gap(s)"
    if verdict.routes:
        summary += " (" + ", ".join(dict.fromkeys(r.kind for r in verdict.routes)) + ")"
    # Same branch as graph.build.after_rule_checks: the planner routes the gaps, or the round limit closes the case.
    target = None if verdict.outcome == "accept" else ("close" if verdict.round >= MAX_ROUNDS else "planner")
    return NodeResult(update, summary, status=status, target_agent=target)
