"""The agent graph: intake → planner → specialists in parallel → brief → knowledge → maintenance planner →
rule checks → critic (revise loop, max 3) → escalation."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agents.close import close_case
from agents.critic import critic
from agents.data import data_agent
from agents.diagnostics import diagnostics
from agents.escalation import escalation
from agents.intake import intake
from agents.knowledge import knowledge
from agents.maint_planner import maint_planner
from agents.planner import planner
from agents.prognostics import prognostics
from agents.rules import rule_checks
from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import MAX_ROUNDS, CaseFile, Claim, Findings

SPECIALISTS = ["data", "prognostics", "diagnostics"]


def after_intake(state: CaseFile) -> str:
    return "planner" if state.status == "running" else END


def after_brief(state: CaseFile) -> str:
    return END if state.status == "error" else "knowledge"


def after_knowledge(state: CaseFile) -> str:
    return END if state.status == "error" else "maint_planner"


def after_rule_checks(state: CaseFile) -> str:
    verdict = state.verdicts[-1]
    if verdict.outcome == "accept":
        return "critic"
    return "close" if state.round >= MAX_ROUNDS else "maint_planner"


def after_critic(state: CaseFile) -> str:
    verdict = state.verdicts[-1]
    if verdict.outcome == "accept":
        return "escalation"
    return "close" if verdict.outcome == "reject" else "maint_planner"


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
    if findings.fault_modes:
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
        claims.append(Claim(text=f"{len(findings.similar_cases)} similar past removals found in case memory.",
                            evidence_ids=findings.similar_cases, confidence="medium"))
    unresolved = state.unresolved([i for c in claims for i in c.evidence_ids])
    if unresolved:
        raise ValueError(f"claims cite unknown evidence {unresolved}")
    return NodeResult({"findings": Findings(claims=claims)}, f"{len(claims)} claims")


def build_graph(checkpointer=None):
    graph = StateGraph(CaseFile)
    graph.add_node("intake", intake)
    graph.add_node("planner", planner)
    graph.add_node("data", data_agent)
    graph.add_node("prognostics", prognostics)
    graph.add_node("diagnostics", diagnostics)
    graph.add_node("brief", brief)
    graph.add_node("knowledge", knowledge)
    graph.add_node("maint_planner", maint_planner)
    graph.add_node("rule_checks", rule_checks)
    graph.add_node("critic", critic)
    graph.add_node("escalation", escalation)
    graph.add_node("close", close_case)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", after_intake, {"planner": "planner", END: END})
    for specialist in SPECIALISTS:
        graph.add_edge("planner", specialist)
    graph.add_edge(SPECIALISTS, "brief")
    graph.add_conditional_edges("brief", after_brief, {"knowledge": "knowledge", END: END})
    graph.add_conditional_edges("knowledge", after_knowledge, {"maint_planner": "maint_planner", END: END})
    graph.add_edge("maint_planner", "rule_checks")
    graph.add_conditional_edges("rule_checks", after_rule_checks,
                                {"critic": "critic", "maint_planner": "maint_planner", "close": "close"})
    graph.add_conditional_edges("critic", after_critic,
                                {"escalation": "escalation", "maint_planner": "maint_planner", "close": "close"})
    graph.add_edge("escalation", END)
    graph.add_edge("close", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
