"""Prognostics: RUL methods A and B and the sensors driving A."""

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile, EvidenceLedger, Findings
from tools.model_tools import ModelTools


@node("prognostics", "estimate_rul", lambda s: f"asset={s.asset.asset_id if s.asset else None}")
def prognostics(state: CaseFile, ctx: RunContext) -> NodeResult:
    asset_id = state.asset.asset_id
    cycles = ctx.data.cycles(asset_id).frame
    ledger = EvidenceLedger("prognostics", state.evidence)
    tools = ModelTools(ctx.pack, ledger)
    rul_ml, rul_trend = tools.rul_ml(asset_id, cycles), tools.rul_trend(asset_id, cycles)
    drivers = tools.drivers(asset_id, cycles)
    anomaly = tools.anomaly(asset_id, cycles)
    findings = Findings(rul_ml=rul_ml, rul_trend=rul_trend, drivers=drivers, anomaly=anomaly)
    summary = (f"RUL A {rul_ml.rul:.0f} ({rul_ml.lower:.0f}-{rul_ml.upper:.0f}), RUL B {rul_trend.rul:.0f}; "
               f"top driver {drivers[0].sensor if drivers else 'none'}"
               + (f"; anomaly {anomaly.residual_sigma:+.1f} sd" if anomaly.flagged else ""))
    return NodeResult({"findings": findings, "evidence": ledger.records}, summary)
