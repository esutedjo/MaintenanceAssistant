"""Planner: records the specialist task list. Round 1 always runs data, prognostics and diagnostics.
Revise rounds are routed straight to the maintenance planner by the graph, not through here."""

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile, Task

SPECIALISTS = [
    ("data", "Telemetry, maintenance history, flight schedule, hangar slots"),
    ("prognostics", "RUL methods A and B, SHAP drivers, anomaly flag"),
    ("diagnostics", "Fault-mode signature match and similar past cases"),
]


@node("planner", "plan_tasks", lambda s: f"goal={s.intent.goal if s.intent else None} asset={s.asset.asset_id if s.asset else None}")
def planner(state: CaseFile, ctx: RunContext) -> NodeResult:
    round_no = 1
    plan = [Task(id=f"T-{agent}", agent=agent, description=description, round=round_no)
            for agent, description in SPECIALISTS]
    return NodeResult({"plan": plan, "round": round_no},
                      f"round {round_no}: {', '.join(a for a, _ in SPECIALISTS)}",
                      target_agent="data,prognostics,diagnostics")
