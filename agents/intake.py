"""Intake: parse the question into an Intent and resolve the asset, or ask back.

The deterministic parser runs first. Only when it finds no engine (and the question is not about the whole fleet or
two engines) does Nemotron, through the critic's Nightshift lane, read the free text. Code then checks the answer:
the quoted words must be in the question and contain the engine's number, and the engine must be in the registry."""

import json
import logging
import re

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import AssetRef, CaseFile, EvidenceLedger, Intent
from tools.data_tools import AssetNotFound, DataTools
from tools.llm import LlmClient, LlmRefusal, LlmUnavailable

log = logging.getLogger("intake")

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
WHICH_ENGINE = "Which engine? Give the asset ID (e.g. FD003-A017) or engine serial number (ESN)."
GOAL_WORDS = {
    "status": ("status", "how is", "health of"),
    "remaining_life": ("how long", "remaining", "rul", "life", "until failure", "time left"),
    "diagnosis": ("what is wrong", "wrong", "fault", "diagnos", "why", "cause"),
    "maintenance_plan": ("what should i do", "plan", "work order", "schedule", "action", "fix", "replace"),
}
CANONICAL = re.compile(r"^(FD\d{3}-[AH]\d{3}|ESN-\d{6})$")
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ORDINALS = {"first": 1, "second": 2, "third": 3}
INTAKE_PROMPT = (
    "You map a maintenance engineer's question to one engine of a turbofan fleet.\n"
    "Engines are named FD001-A001 to FD001-A100 and FD003-A001 to FD003-A100 (FD001 and FD003 are the two fleets; "
    "A is the active fleet), or by serial number ESN-7xxxxx.\n"
    "Reply with one JSON object only, no markdown: "
    "{\"engine\": \"FD00x-A###\" or \"ESN-######\" or null, \"quote\": \"the exact words of the question that name "
    "the engine\"}.\n"
    "Use null unless the question names exactly one engine. Never guess a fleet or a number that is not in the "
    "question.\nQuestion: "
)


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
                      clarification=FLEET_QUESTION if fleet_question else WHICH_ENGINE)
    return Intent(goal=goal, asset_id=asset_id)


def numbers_in(text: str) -> set[int]:
    """Numbers written as digits or as simple English words ("thirty-one", "the third fleet")."""
    found = {int(n) for n in re.findall(r"\d+", text)}
    words = re.findall(r"[a-z]+", text.lower())
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if word in TENS:
            found.add(TENS[word] + (NUMBER_WORDS[nxt] if NUMBER_WORDS.get(nxt, 10) < 10 else 0))
        elif word in NUMBER_WORDS:
            found.add(NUMBER_WORDS[word])
        elif word in ORDINALS:
            found.add(ORDINALS[word])
        if word == "hundred":
            found.add(100)
    return found


def grounded_engine(question: str, reply: dict) -> tuple[str | None, str]:
    """Accept the model's engine only if code can see it in the question. Returns (engine or None, reason)."""
    engine = str(reply.get("engine") or "").strip().upper()
    quote = " ".join(str(reply.get("quote") or "").split())
    if not engine:
        return None, "the model named no engine"
    if not CANONICAL.match(engine):
        return None, f"{engine} is not an engine ID"
    if not quote or quote.lower() not in " ".join(question.split()).lower():
        return None, f"the quoted words are not in the question: {quote!r}"
    numbers = numbers_in(quote)
    if engine.startswith("ESN-"):
        if int(engine[4:]) not in numbers:
            return None, f"the serial number of {engine} is not in the quote"
        return engine, ""
    fleet, unit = int(engine[2:5]), int(engine[7:10])
    if unit not in numbers:
        return None, f"unit {unit} of {engine} is not in the quote"
    if fleet not in numbers_in(question) and f"fd{fleet:03d}" not in question.lower().replace(" ", ""):
        return None, f"fleet FD{fleet:03d} is not named in the question"
    return engine, ""


def _json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value


def read_engine_with_llm(question: str, ctx: RunContext) -> tuple[str | None, str | None, str]:
    """Ask the critic's Nightshift lane which engine the question names. Returns (engine, model, note)."""
    if ctx.settings.sample_mode:
        return None, None, "sample mode"
    client = LlmClient(ctx.settings)
    reply = None
    try:
        if not client.configured:
            return None, None, "no Nightshift token"
        reply = client.chat([{"role": "user", "content": INTAKE_PROMPT + question[:500]}],
                            model=ctx.settings.meterless_critic_model, temperature=0, max_tokens=200,
                            app=ctx.settings.meterless_critic_app, response_format={"type": "json_object"},
                            timeout_s=ctx.settings.critic_timeout_s)
        engine, reason = grounded_engine(question, _json(reply.text))
        return engine, reply.model, reason
    except (LlmUnavailable, LlmRefusal, ValueError, json.JSONDecodeError) as error:
        log.warning("llm intake skipped (%s: %s)", type(error).__name__, str(error)[:200])
        return None, (reply.model if reply else None), f"LLM intake unavailable ({type(error).__name__})"
    finally:
        client.close()


def _ask_back(intent: Intent, model: str | None = None) -> NodeResult:
    return NodeResult({"intent": intent, "status": "needs_clarification"}, intent.clarification,
                      status="needs_revision", target_agent="engineer", model=model)


@node("intake", "parse_and_resolve", lambda s: s.question[:200])
def intake(state: CaseFile, ctx: RunContext) -> NodeResult:
    intent = parse(state.question)
    model, note = None, ""
    # "engine thirty-one of the first fleet" sounds like a fleet question; the model gets to read it too, and the
    # fleet answer stands when it finds no engine that code can ground in the question.
    if intent.needs_clarification and intent.clarification in {WHICH_ENGINE, FLEET_QUESTION}:
        engine, model, note = read_engine_with_llm(state.question, ctx)
        if engine is None:
            return _ask_back(intent, model)
        intent = intent.model_copy(update={"asset_id": engine, "needs_clarification": False, "clarification": None,
                                           "resolved_by": model})
    if intent.needs_clarification:
        return _ask_back(intent)
    ledger = EvidenceLedger("intake", state.evidence)
    tools = DataTools(ctx.data, ledger, ctx.pack, ctx.settings.reference_time)
    try:
        profile = tools.asset_profile(intent.asset_id)
    except AssetNotFound:
        intent = intent.model_copy(update={
            "needs_clarification": True,
            "clarification": f"No engine {intent.asset_id} in the asset registry. Check the ID or serial number."})
        return _ask_back(intent, model)
    content = profile.content
    if content["asset_type"] != ctx.pack["asset_type"]:
        raise ValueError(f"{content['asset_id']} is a {content['asset_type']}; loaded asset pack is {ctx.pack['asset_type']}")
    asset = AssetRef(asset_id=content["asset_id"], asset_type=content["asset_type"],
                     pack_version=ctx.pack["version"], variant=content["dataset"])
    intent = intent.model_copy(update={"asset_id": asset.asset_id})
    summary = f"goal={intent.goal}; {profile.summary}"
    if intent.resolved_by:
        summary += f"; engine read from free text by {intent.resolved_by}, checked against the question and registry"
    return NodeResult({"intent": intent, "asset": asset, "evidence": ledger.records}, summary,
                      target_agent="data,prognostics,diagnostics", model=model)
