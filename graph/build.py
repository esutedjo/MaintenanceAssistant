"""The agent graph. Intake → planner → data, prognostics and diagnostics in parallel → brief → planner review, which
delegates follow-ups only when the findings call for them (data → prognostics/diagnostics) → knowledge (→ diagnostics
when an inspection finding needs checking against the fault mode, once Knowledge has compared it with its limit) →
maintenance planner → rule checks → critic → escalation. When a gate sends the brief back, the planner routes each gap by kind to
the agent that can fix it (data, knowledge, diagnostics, prognostics or the maintenance planner), the maintenance
planner rebuilds the plan, and the rule checks and the critic run again: at most MAX_ROUNDS rounds, then the case
closes as insufficient evidence."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agents.close import close_case
from agents.critic import critic
from agents.data import data_agent, data_task
from agents.diagnostics import diagnostics, diagnostics_task, similar_cases_claim
from agents.escalation import escalation
from agents.intake import intake
from agents.knowledge import knowledge, knowledge_task
from agents.maint_planner import maint_planner
from agents.planner import TASK_AGENTS, planner, review, route_gaps
from agents.prognostics import prognostics, prognostics_task
from agents.rules import rule_checks
from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import MAX_ROUNDS, CaseFile, Claim, Findings
from casefile.wear import no_wear_label, no_wear_text

SPECIALISTS = ["data", "prognostics", "diagnostics"]
TASK_NODES = [f"{agent}_task" for agent in TASK_AGENTS]


def after_intake(state: CaseFile) -> str:
    return "planner" if state.status == "running" else END


def after_brief(state: CaseFile) -> str:
    return END if state.status == "error" else "review"


def _pending(state: CaseFile, agents: tuple[str, ...]) -> list[str]:
    """Task nodes with work they can do now (a check that needs Knowledge's limit section waits for Knowledge)."""
    return [f"{a}_task" for a in agents if state.ready(a)]


def _knowledge_ran(state: CaseFile) -> bool:
    return any(e.agent_name == "knowledge" for e in state.trace)


def after_review(state: CaseFile) -> list[str] | str:
    """Data follow-ups first (they pull what the others recompute on), then prognostics and diagnostics."""
    return _pending(state, ("data",)) or _pending(state, ("prognostics", "diagnostics")) or "knowledge"


def after_data_task(state: CaseFile) -> list[str] | str:
    """Follow-ups (before any verdict) go on to the prognostics and diagnostics follow-ups that recompute on what the
    data agent pulled, else to knowledge; repairs go back to the maintenance planner."""
    if state.status == "error":
        return END
    if state.verdicts:
        return "maint_planner"
    return _pending(state, ("prognostics", "diagnostics")) or "knowledge"


def after_model_task(state: CaseFile) -> str:
    if state.status == "error":
        return END
    return "maint_planner" if state.verdicts or _knowledge_ran(state) else "knowledge"


def after_knowledge(state: CaseFile) -> str:
    """An inspection check runs once Knowledge has compared each finding with its limit; then the plan."""
    if state.status == "error":
        return END
    return "diagnostics_task" if _pending(state, ("diagnostics",)) else "maint_planner"


def after_rule_checks(state: CaseFile) -> str:
    verdict = state.verdicts[-1]
    if verdict.outcome == "accept":
        return "critic"
    # A brief sent back in round 3 closes; earlier rounds get a repair and a new plan (at most MAX_ROUNDS plans).
    return "close" if verdict.round >= MAX_ROUNDS else "route"


def after_critic(state: CaseFile) -> str:
    verdict = state.verdicts[-1]
    if verdict.outcome == "accept":
        return "escalation"
    return "close" if verdict.outcome == "reject" else "route"


def after_route(state: CaseFile) -> list[str] | str:
    """The agents the planner routed this round's gaps to; the maintenance planner alone when only it can fix them;
    close when no agent can."""
    if not any(t.round == state.round and t.status == "pending" for t in state.plan if t.id.startswith("T-fix-")):
        return "close"
    return _pending(state, ("data", "knowledge", "diagnostics", "prognostics")) or "maint_planner"


def rul_claims(findings: Findings, divergence_limit: float) -> list[Claim]:
    a, b = findings.rul_ml, findings.rul_trend
    if a is None or b is None:
        return []
    gap = abs(a.rul - b.rul)
    claims = [Claim(text=f"Remaining useful life is about {a.rul:.0f} cycles (10th-90th percentile "
                         f"{a.lower:.0f}-{a.upper:.0f}) by the ML model; the health-index trend gives {b.rul:.0f}.",
                    evidence_ids=[a.evidence_id, b.evidence_id], confidence="medium" if gap <= divergence_limit else "low")]
    if gap > divergence_limit:
        claims.append(Claim(text=f"The two RUL methods differ by {gap:.0f} cycles (limit {divergence_limit:.0f}); "
                                 "treat the estimate as uncertain.",
                            evidence_ids=[a.evidence_id, b.evidence_id], confidence="low"))
    return claims


@node("brief", "assemble_brief", lambda s: f"evidence={len(s.evidence)}")
def brief(state: CaseFile, ctx: RunContext) -> NodeResult:
    if any(event.status == "error" for event in state.trace):
        return NodeResult({"status": "error"}, "a specialist failed; see trace", status="error")
    findings = state.findings
    claims = rul_claims(findings, ctx.pack["thresholds"]["rul_divergence_cycles"])
    # A healthy engine on a one-mode fleet: the matcher's 1.00 is by construction, so say no wear is detected (an
    # entered inspection finding is not classified yet here, so it keeps the fault-mode wording).
    inspected = any(e.kind == "inspection" for e in state.evidence.values())
    variant = state.asset.variant if state.asset else None
    quiet = None if inspected else no_wear_label(findings.model_dump(), ctx.pack, variant)
    if findings.fault_modes and quiet:
        top = findings.fault_modes[0]
        claims.append(Claim(text=f"{no_wear_text(quiet)}.",
                            evidence_ids=[top.evidence_id, findings.rul_ml.evidence_id, findings.rul_trend.evidence_id],
                            confidence="high"))
    elif findings.fault_modes:
        top = findings.fault_modes[0]
        label = ctx.pack["failure_modes"][top.failure_mode]["label"]
        claims.append(Claim(text=f"Most likely fault mode: {label} (probability {top.probability:.2f}).",
                            evidence_ids=[top.evidence_id], confidence="high" if top.probability >= 0.85 else "medium"))
    if findings.drivers:
        names = ", ".join(f"{ctx.pack['sensors'][d.sensor]['name']} ({d.component})" for d in findings.drivers[:3])
        claims.append(Claim(text=f"Sensors pulling the RUL estimate down most: {names}.",
                            evidence_ids=[findings.drivers[0].evidence_id], confidence="medium"))
    if findings.anomaly and findings.anomaly.flagged:
        claims.append(Claim(text=f"Health index is {findings.anomaly.residual_sigma:.1f} sd above the fleet median "
                                 f"at cycle {findings.anomaly.cycle}.",
                            evidence_ids=[findings.anomaly.evidence_id], confidence="medium"))
    if findings.similar_cases:
        # What the shop found on each cited past removal, not only how many there were (casefile/shop_findings.py).
        claims.append(Claim(text=similar_cases_claim(state.evidence, findings.similar_cases,
                                                     f"{len(findings.similar_cases)} similar past removals found in "
                                                     "case memory."),
                            evidence_ids=findings.similar_cases, confidence="medium"))
    unresolved = state.unresolved([i for c in claims for i in c.evidence_ids])
    if unresolved:
        raise ValueError(f"claims cite unknown evidence {unresolved}")
    return NodeResult({"findings": Findings(claims=claims)}, f"{len(claims)} claims", target_agent="planner")


def build_graph(checkpointer=None):
    graph = StateGraph(CaseFile)
    graph.add_node("intake", intake)
    graph.add_node("planner", planner)
    graph.add_node("data", data_agent)
    graph.add_node("prognostics", prognostics)
    graph.add_node("diagnostics", diagnostics)
    graph.add_node("brief", brief)
    graph.add_node("review", review)
    graph.add_node("data_task", data_task)
    graph.add_node("prognostics_task", prognostics_task)
    graph.add_node("diagnostics_task", diagnostics_task)
    graph.add_node("knowledge", knowledge)
    graph.add_node("knowledge_task", knowledge_task)
    graph.add_node("maint_planner", maint_planner)
    graph.add_node("rule_checks", rule_checks)
    graph.add_node("critic", critic)
    graph.add_node("route", route_gaps)
    graph.add_node("escalation", escalation)
    graph.add_node("close", close_case)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", after_intake, {"planner": "planner", END: END})
    for specialist in SPECIALISTS:
        graph.add_edge("planner", specialist)
    graph.add_edge(SPECIALISTS, "brief")
    graph.add_conditional_edges("brief", after_brief, {"review": "review", END: END})
    graph.add_conditional_edges("review", after_review, [*TASK_NODES, "knowledge"])
    graph.add_conditional_edges("data_task", after_data_task,
                                ["prognostics_task", "diagnostics_task", "knowledge", "maint_planner", END])
    for task_node in ("prognostics_task", "diagnostics_task"):
        graph.add_conditional_edges(task_node, after_model_task, ["knowledge", "maint_planner", END])
    graph.add_conditional_edges("knowledge", after_knowledge,
                                {"maint_planner": "maint_planner", "diagnostics_task": "diagnostics_task", END: END})
    graph.add_edge("knowledge_task", "maint_planner")
    graph.add_edge("maint_planner", "rule_checks")
    graph.add_conditional_edges("rule_checks", after_rule_checks,
                                {"critic": "critic", "route": "route", "close": "close"})
    graph.add_conditional_edges("critic", after_critic,
                                {"escalation": "escalation", "route": "route", "close": "close"})
    graph.add_conditional_edges("route", after_route, [*TASK_NODES, "maint_planner", "close"])
    graph.add_edge("escalation", END)
    graph.add_edge("close", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
