"""Data agent: telemetry trend, input plausibility, maintenance history, flight schedule and hangar slots as Evidence."""

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile, EvidenceLedger
from tools.data_tools import DataTools


def _profile(state: CaseFile) -> dict:
    """Asset registry row the intake agent already read (last cycle, fleet status)."""
    return next((e.content for e in state.evidence.values()
                 if e.produced_by == "intake" and "last_cycle" in e.content), {})


@node("data", "query_sources", lambda s: f"asset={s.asset.asset_id if s.asset else None}")
def data_agent(state: CaseFile, ctx: RunContext) -> NodeResult:
    asset_id = state.asset.asset_id
    ledger = EvidenceLedger("data", state.evidence)
    tools = DataTools(ctx.data, ledger, ctx.pack, ctx.settings.reference_time)
    telemetry = tools.telemetry(asset_id)
    quality = tools.data_quality(asset_id, telemetry.cycles, _profile(state))
    tools.history(asset_id)
    tools.flight_schedule(asset_id)
    tools.free_slots()
    fallback = any(e.degraded for e in ledger.records.values() if e.kind == "query")
    summary = f"{len(ledger.records)} evidence records via {ctx.data.mode}; input data {quality.content['data_quality']}"
    return NodeResult({"evidence": ledger.records}, summary, status="fallback" if fallback else "success")
