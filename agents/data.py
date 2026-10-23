"""Data agent: telemetry trend, maintenance history, flight schedule and hangar slots as Evidence."""

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile, EvidenceLedger
from tools.data_tools import DataTools


@node("data", "query_sources", lambda s: f"asset={s.asset.asset_id if s.asset else None}")
def data_agent(state: CaseFile, ctx: RunContext) -> NodeResult:
    asset_id = state.asset.asset_id
    ledger = EvidenceLedger("data", state.evidence)
    tools = DataTools(ctx.data, ledger, ctx.pack, ctx.settings.reference_time)
    tools.telemetry(asset_id)
    tools.history(asset_id)
    tools.flight_schedule(asset_id)
    tools.free_slots()
    status = "fallback" if any(e.degraded for e in ledger.records.values()) else "success"
    return NodeResult({"evidence": ledger.records}, f"{len(ledger.records)} evidence records via {ctx.data.mode}",
                      status=status)
