"""Intake: parse the question into an Intent and resolve the asset, or ask back."""

import re

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import AssetRef, CaseFile, EvidenceLedger, Intent
from tools.data_tools import AssetNotFound, DataTools

ASSET_PATTERN = re.compile(r"\b(FD\d{3}-[AH]\d{3})\b", re.IGNORECASE)
ESN_PATTERN = re.compile(r"\bESN[- ]?(\d{6})\b", re.IGNORECASE)
GOAL_WORDS = {
    "status": ("status", "how is", "health of"),
    "remaining_life": ("how long", "remaining", "rul", "life", "until failure", "time left"),
    "diagnosis": ("what is wrong", "wrong", "fault", "diagnos", "why", "cause"),
    "maintenance_plan": ("what should i do", "plan", "work order", "schedule", "action", "fix", "replace"),
}


def parse(question: str) -> Intent:
    text = question.lower()
    goals = [goal for goal, words in GOAL_WORDS.items() if any(w in text for w in words)]
    goal = goals[0] if len(goals) == 1 else "full_assessment"
    asset = ASSET_PATTERN.search(question)
    esn = ESN_PATTERN.search(question)
    asset_id = asset.group(1).upper() if asset else f"ESN-{esn.group(1)}" if esn else None
    if asset_id is None:
        return Intent(goal=goal, asset_id=None, needs_clarification=True,
                      clarification="Which engine? Give the asset ID (e.g. FD003-A017) or engine serial number (ESN).")
    return Intent(goal=goal, asset_id=asset_id)


@node("intake", "parse_and_resolve", lambda s: s.question[:200])
def intake(state: CaseFile, ctx: RunContext) -> NodeResult:
    intent = parse(state.question)
    if intent.needs_clarification:
        return NodeResult({"intent": intent, "status": "needs_clarification"}, intent.clarification,
                          status="needs_revision", target_agent="engineer")
    ledger = EvidenceLedger("intake", state.evidence)
    tools = DataTools(ctx.data, ledger, ctx.pack, ctx.settings.reference_time)
    try:
        profile = tools.asset_profile(intent.asset_id)
    except AssetNotFound:
        intent = intent.model_copy(update={
            "needs_clarification": True,
            "clarification": f"No engine {intent.asset_id} in the asset registry. Check the ID or serial number."})
        return NodeResult({"intent": intent, "status": "needs_clarification"}, intent.clarification,
                          status="needs_revision", target_agent="engineer")
    content = profile.content
    if content["asset_type"] != ctx.pack["asset_type"]:
        raise ValueError(f"{content['asset_id']} is a {content['asset_type']}; loaded asset pack is {ctx.pack['asset_type']}")
    asset = AssetRef(asset_id=content["asset_id"], asset_type=content["asset_type"],
                     pack_version=ctx.pack["version"], variant=content["dataset"])
    intent = intent.model_copy(update={"asset_id": asset.asset_id})
    return NodeResult({"intent": intent, "asset": asset, "evidence": ledger.records},
                      f"goal={intent.goal}; {profile.summary}", target_agent="data,prognostics,diagnostics")
