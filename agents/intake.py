"""Intake: parse the question into an Intent and resolve the asset, or ask back."""

import re

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import AssetRef, CaseFile, EvidenceLedger, Intent
from tools.data_tools import AssetNotFound, DataTools

# Accepts FD003-A100 and the ways people type it: FD003 A100, fd003_a100.
ASSET_PATTERN = re.compile(r"\bFD\s?(\d{3})\s?[-_ ]?\s?([AH])\s?-?\s?(\d{3})\b", re.IGNORECASE)
ESN_PATTERN = re.compile(r"\bESN[- ]?(\d{6})\b", re.IGNORECASE)
# "engine 31 in the FD001 fleet": an active-fleet unit number plus a fleet name.
FLEET_PATTERN = re.compile(r"\bFD\s?(\d{3})\b", re.IGNORECASE)
UNIT_PATTERN = re.compile(r"\b(?:engine|unit)\s*(?:no\.?|number|#)?\s*(\d{1,3})\b", re.IGNORECASE)
FLEET_WORDS = ("which engine", "what engines", "fleet", "all engines", "any engine", "need attention", "most urgent",
               "worst engine", "rank")
FLEET_QUESTION = ("This asks about the whole fleet. The Fleet tab ranks every active engine by urgency; "
                  "or name one engine (e.g. FD003-A017) for its brief.")
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
    assets = list(dict.fromkeys(f"FD{fd}-{kind.upper()}{num}" for fd, kind, num in ASSET_PATTERN.findall(question)))
    fleet, unit = FLEET_PATTERN.search(question), UNIT_PATTERN.search(question)
    if not assets and fleet and unit:
        assets = [f"FD{fleet.group(1)}-A{int(unit.group(1)):03d}"]
    if len(assets) > 1:
        return Intent(goal=goal, asset_id=None, needs_clarification=True,
                      clarification=f"You named {', '.join(assets)}. Cleris answers for one engine at a time; "
                                    "ask about one of them.")
    esn = ESN_PATTERN.search(question)
    asset_id = assets[0] if assets else f"ESN-{esn.group(1)}" if esn else None
    if asset_id is None:
        fleet_question = any(w in text for w in FLEET_WORDS)
        return Intent(goal=goal, asset_id=None, needs_clarification=True,
                      clarification=FLEET_QUESTION if fleet_question else
                      "Which engine? Give the asset ID (e.g. FD003-A017) or engine serial number (ESN).")
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
