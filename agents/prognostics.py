"""Prognostics: RUL methods A and B and the sensors driving A. Also runs the tasks the planner delegates: a back-test
of both methods at the last inspection when they disagree, and a recompute when a gate finds a reported RUL that does
not match its model evidence."""

from agents.runtime import NodeResult, RunContext, node, run_tasks
from casefile.schemas import CaseFile, EvidenceLedger, Findings, Task
from tools.model_tools import ModelTools

SEVERITY = ["none", "minor", "moderate", "severe"]


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


def backtest(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Follow-up for a disagreement: both methods recomputed on the telemetry up to the last inspection (the data
    agent's back-test point), then checked against the engine's own record. A method is contradicted when it gave
    fewer cycles than the engine has flown since (it is still flying), or when it gave at least the moderate band
    while that inspection found moderate or severe wear (it missed wear a borescope saw)."""
    asset_id = state.asset.asset_id
    point = next(e for e in reversed(list(state.evidence.values())) if "reference_cycle" in e.content)
    ref, since = int(point.content["reference_cycle"]), int(point.content["cycles_since"])
    found = point.content.get("reference_severity") or "none"
    cycles = ctx.data.cycles(asset_id).frame
    window = cycles[cycles.cycle <= ref]
    models = ModelTools(ctx.pack, ledger).models
    then = {"A": models.rul_a.predict(window)[0], "B": models.rul_b.predict(window)[0]}
    now = {"A": state.findings.rul_ml, "B": state.findings.rul_trend}
    moderate = ctx.pack["thresholds"]["moderate_rul_cycles"]
    wear = SEVERITY.index(found) >= SEVERITY.index("moderate") if found in SEVERITY else False
    contradicted = {}
    for name, estimate in then.items():
        if estimate.rul < since:
            contradicted[name] = f"gave {estimate.rul:.0f} cycles at cycle {ref}, but the engine has flown {since} since"
        elif wear and estimate.rul >= moderate:
            contradicted[name] = (f"gave {estimate.rul:.0f} cycles at cycle {ref} while that inspection found "
                                  f"{found} wear")
    optimistic = "A" if now["A"].rul >= now["B"].rul else "B"
    missed_wear = optimistic in contradicted and wear
    text = ", ".join(f"RUL {k} then {v.rul:.0f}" + (" (cap)" if v.at_cap else "") for k, v in then.items())
    outcome = ("; ".join(f"{k} {why}" for k, why in contradicted.items()) if contradicted
               else "neither method is contradicted by the record; the borescope decides")
    summary = (f"Back-test at cycle {ref} ({point.content.get('reference_wo') or 'no inspection'}, finding {found}): "
               f"{text}; {since} cycles flown since; {outcome}")
    evidence = ledger.add("model", f"model:backtest@{ctx.pack['asset_type']}/{ctx.pack['version']}", summary, {
        "asset_id": asset_id, "reference_cycle": ref, "cycles_since": since, "reference_severity": found,
        "rul_a_then": then["A"].rul, "rul_b_then": then["B"].rul, "rul_a_now": now["A"].rul, "rul_b_now": now["B"].rul,
        "contradicted": contradicted, "optimistic_method": optimistic,
        "optimistic_missed_wear": missed_wear, "from": point.id})
    return f"{evidence.id}: {outcome}", None


def recompute(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Repair for a reported RUL that does not match its model evidence: both methods again, from the models."""
    asset_id = state.asset.asset_id
    cycles = ctx.data.cycles(asset_id).frame
    tools = ModelTools(ctx.pack, ledger)
    rul_ml, rul_trend = tools.rul_ml(asset_id, cycles), tools.rul_trend(asset_id, cycles)
    return (f"recomputed RUL A {rul_ml.rul:.0f} ({rul_ml.lower:.0f}-{rul_ml.upper:.0f}) as {rul_ml.evidence_id}, "
            f"RUL B {rul_trend.rul:.0f} as {rul_trend.evidence_id}"), Findings(rul_ml=rul_ml, rul_trend=rul_trend)


@node("prognostics", "follow_up", lambda s: f"tasks={[t.kind for t in s.pending('prognostics')]}")
def prognostics_task(state: CaseFile, ctx: RunContext) -> NodeResult:
    return run_tasks(state, ctx, "prognostics", {"backtest": backtest, "repair": recompute})
