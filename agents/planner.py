"""Planner: decides who works next and why. Round 1 always runs data, prognostics and diagnostics in parallel.
It then reviews their findings and delegates follow-up tasks only when the context calls for them (the two RUL
methods disagree, the fault mode is unclear, the input data failed a plausibility check); a clear case goes straight
on to knowledge. When a gate sends the brief back, it routes each gap, by kind, to the agent that can fix it
(assurance/routing.py). Every delegation is a handoff line in the trace with its target agent and reason."""

from agents.runtime import NodeResult, RunContext, node
from assurance.routing import ROUTES
from casefile.schemas import CaseFile, Task

SPECIALISTS = [
    ("data", "Telemetry, maintenance history, flight schedule, hangar slots"),
    ("prognostics", "RUL methods A and B, SHAP drivers, anomaly flag"),
    ("diagnostics", "Fault-mode signature match and similar past cases"),
]
# Runs once after the specialists: what applies depends on their fault mode and RUL lower bound.
FOLLOW_UP = [("knowledge", "Applicable documents in precedence order; mandatory actions")]
# Below this top fault-mode probability the diagnosis is unclear (the brief's own line for "high" confidence).
CLEAR_FAULT = 0.85
TASK_AGENTS = ("data", "knowledge", "prognostics", "diagnostics")


@node("planner", "plan_tasks", lambda s: f"goal={s.intent.goal if s.intent else None} asset={s.asset.asset_id if s.asset else None}")
def planner(state: CaseFile, ctx: RunContext) -> NodeResult:
    round_no = 1
    plan = [Task(id=f"T-{agent}", agent=agent, description=description, round=round_no)
            for agent, description in [*SPECIALISTS, *FOLLOW_UP]]
    return NodeResult({"plan": plan, "round": round_no},
                      f"round {round_no}: {', '.join(a for a, _ in SPECIALISTS)}; then review the findings; then "
                      f"{', '.join(a for a, _ in FOLLOW_UP)}",
                      target_agent="data,prognostics,diagnostics")


def _degradation_detected(state: CaseFile) -> bool:
    modes = state.findings.fault_modes
    evidence = state.evidence.get(modes[0].evidence_id) if modes else None
    return bool(evidence and evidence.content.get("degradation_detected"))


def _data_quality(state: CaseFile):
    return next((e for e in state.evidence.values() if e.produced_by == "data" and "data_quality" in e.content), None)


def follow_ups(state: CaseFile, pack: dict) -> tuple[list[Task], list[str]]:
    """Follow-up tasks the round-1 findings call for, and why the others were not needed."""
    th = pack["thresholds"]
    a, b, modes = state.findings.rul_ml, state.findings.rul_trend, state.findings.fault_modes
    tasks, clear = [], []
    if a is not None and b is not None:
        gap, limit = abs(a.rul - b.rul), th["rul_divergence_cycles"]
        if gap > limit:
            why = f"RUL A {a.rul:.0f} and RUL B {b.rul:.0f} differ by {gap:.0f} cycles (limit {limit:.0f})"
            tasks += [
                Task(id="T-follow-data-history", agent="data", kind="inspection_history",
                     description="Inspection history and the telemetry up to the last inspection",
                     reason=f"{why}; pull the inspection history and the telemetry up to the last inspection"),
                Task(id="T-follow-prognostics-backtest", agent="prognostics", kind="backtest",
                     description="Recompute both methods at the last inspection and check them against its finding",
                     reason=f"{why}; recompute both methods on the telemetry up to the last inspection and check "
                            "each against what that inspection found and the cycles flown since"),
            ]
        else:
            clear.append(f"RUL methods agree within {limit:.0f} cycles (A {a.rul:.0f}, B {b.rul:.0f})")
    if modes:
        top = modes[0]
        second = modes[1] if len(modes) > 1 else None
        close = second is not None and top.probability - second.probability < th["ambiguous_fault_probability"]
        if second is not None and (top.probability < CLEAR_FAULT or close) and _degradation_detected(state):
            tasks.append(Task(
                id="T-follow-diagnostics-candidates", agent="diagnostics", kind="check_candidates",
                description="Discriminating sensors over a longer window and past removals for both candidates",
                reason=(f"fault mode unclear: {top.failure_mode} {top.probability:.2f} vs {second.failure_mode} "
                        f"{second.probability:.2f}; check the sensors that tell them apart over 30 cycles and "
                        "past removals for both")))
        elif second is not None and (top.probability < CLEAR_FAULT or close):
            clear.append(f"fault mode {top.failure_mode} p={top.probability:.2f}, but the health index is still in "
                         "the healthy band, so the signature is weak and a sensor check would not settle it")
        else:
            clear.append(f"fault mode clear ({top.failure_mode} p={top.probability:.2f})")
    quality = _data_quality(state)
    if quality is not None and quality.content.get("data_quality") not in (None, "ok"):
        issue = (quality.content.get("issues") or [{}])[0].get("detail", "")
        tasks.append(Task(
            id="T-follow-data-reread", agent="data", kind="reread_input",
            description="Re-read the inputs without the run's cache and repeat the plausibility checks",
            reason=(f"input data {quality.content['data_quality']} ({issue}); re-read it, skipping the run's cache, "
                    "to tell a transient read error from bad source data")))
    elif quality is not None:
        clear.append("input data plausible")
    return tasks, clear


@node("planner", "review_findings", lambda s: f"claims={len(s.findings.claims)} evidence={len(s.evidence)}")
def review(state: CaseFile, ctx: RunContext) -> NodeResult:
    """After round 1: add follow-up tasks only where the findings call for them."""
    tasks, clear = follow_ups(state, ctx.pack)
    if not tasks:
        return NodeResult({}, "no follow-up needed: " + "; ".join(clear), target_agent="knowledge")
    targets = list(dict.fromkeys(t.agent for t in tasks))
    summary = (f"{len(tasks)} follow-up task(s): " + "; ".join(f"{t.agent} {t.kind}" for t in tasks)
               + (f"; already clear: {'; '.join(clear)}" if clear else ""))
    return NodeResult({"plan": tasks}, summary, target_agent=",".join(targets),
                      handoffs=[("delegate", t.agent, f"{t.kind}: {t.reason}") for t in tasks])


@node("planner", "route_gaps", lambda s: f"round={s.round} gate={s.verdicts[-1].gate if s.verdicts else None}")
def route_gaps(state: CaseFile, ctx: RunContext) -> NodeResult:
    """After a gate sends the brief back: each gap to the agent that can fix it; then the maintenance planner
    rebuilds the plan on what they found, and the rule checks and the critic run again."""
    verdict = state.verdicts[-1]
    by_target: dict[str | None, list] = {}
    for route in verdict.routes:
        by_target.setdefault(route.target, []).append(route)
    tasks, handoffs = [], []
    for target, routes in by_target.items():
        if target is None:
            continue
        kinds = list(dict.fromkeys(r.kind for r in routes))
        fix = "; ".join(dict.fromkeys(ROUTES[k][1] for k in kinds))
        tasks.append(Task(id=f"T-fix-{state.round}-{target}", agent=target, kind=",".join(kinds), round=state.round,
                          description=fix, reason=" | ".join(r.gap for r in routes)[:800]))
        handoffs.append(("route", target, f"{', '.join(kinds)} from {verdict.gate.replace('_', ' ')} round "
                                          f"{verdict.round}: {fix}. Gap: {routes[0].gap[:240]}"))
    unfixable = by_target.get(None, [])
    if not tasks:
        kinds = ", ".join(dict.fromkeys(r.kind for r in unfixable))
        return NodeResult({}, f"no agent can fix {kinds}: {ROUTES[unfixable[0].kind][1]}; closing the case",
                          target_agent="close", status="rejected")
    if all(t.agent != "maint_planner" for t in tasks):
        tasks.append(Task(id=f"T-fix-{state.round}-maint_planner", agent="maint_planner", kind="rebuild",
                          round=state.round, description="rebuild the plan on the evidence the fixing agents added",
                          reason="the plan must cite the repaired evidence"))
        handoffs.append(("route", "maint_planner", "rebuild the plan on the evidence the fixing agents add; "
                                                   "then rule checks and the critic again"))
    summary = (f"round {state.round}: " + "; ".join(f"{t.kind} -> {t.agent}" for t in tasks)
               + (f"; no agent can fix {', '.join(r.kind for r in unfixable)}, left open" if unfixable else ""))
    return NodeResult({"plan": tasks}, summary, target_agent=",".join(t.agent for t in tasks), handoffs=handoffs)
