"""Close the case when the critic will not accept after MAX_ROUNDS."""

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile


@node("close", "insufficient_evidence", lambda s: f"round={s.round}")
def close_case(state: CaseFile, ctx: RunContext) -> NodeResult:
    gaps = [g for v in state.verdicts if v.outcome != "accept" for g in v.gaps]
    unique = list(dict.fromkeys(gaps))
    return NodeResult({"status": "insufficient_evidence"},
                      f"closed after {state.round} round(s); gaps: {'; '.join(unique) or 'none'}",
                      status="rejected")
