"""Escalation graph node. Wraps assurance.escalation; never writes the recommendation."""

from agents.runtime import NodeResult, RunContext, node
from assurance.escalation import run_escalations
from casefile.schemas import CaseFile


@node("escalation", "apply_rules", lambda s: f"rec={s.recommendation.action if s.recommendation else None}")
def escalation(state: CaseFile, ctx: RunContext) -> NodeResult:
    items = run_escalations(state, ctx.pack)
    urgent = any(e.severity == "urgent" for e in items)
    status = "success"
    summary = f"{len(items)} escalation(s)" + ("; URGENT" if urgent else "")
    if items:
        summary += ": " + "; ".join(e.rule for e in items)
    return NodeResult({"escalations": items, "status": "needs_review"}, summary, status=status)
