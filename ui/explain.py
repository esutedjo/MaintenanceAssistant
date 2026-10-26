"""Ask Cleris to explain: Nemotron, on the critic's Nightshift lane, explains the brief on screen in plain words.

The model gets the question, the glossary and the brief's on-screen fields only, and is told to explain, not to
advise or change the decision. Code then checks the answer: every number, date and identifier in it must appear in
what the model was given, or it is flagged. In sample mode, or when Nightshift does not answer, the glossary answers
instead and says so."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from assurance.challenges import IDENTIFIER, claim_numbers
from settings import Settings
from tools.llm import LlmClient, LlmRefusal, LlmUnavailable
from ui.brief import (check_summary, decision_boundaries, escalation_view, fault_label, fmt_rul, rul_context,
                      verdict_headline, verdict_meta, verdict_tone, world_eligibility)
from ui.help import entry_line, glossary, plain_summary, search_glossary

log = logging.getLogger("explain")

PROMPT = (
    "You explain an aircraft engine maintenance decision brief to a reader who is not an engineer or a data "
    "scientist.\n"
    "Rules:\n"
    "- Explain only. Do not recommend actions, do not change, rank or question the decision, and do not tell the "
    "reader to approve or reject.\n"
    "- Use only the brief facts and the glossary below. If they do not answer the question, say so.\n"
    "- Do not state a number, date or ID that is not written below, and do not calculate new numbers.\n"
    "- Plain words, at most 150 words, no tables.\n"
)
TONE_COLOUR = {"urgent": "red (act now)", "warning": "amber (plan soon or inspect first)", "ok": "green (keep flying)",
               "hold": "grey (no recommendation)"}
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# IDENTIFIER without its numeric-range branch, so "5-11" still yields 5 and 11 while SB-TF-72-0142 yields nothing.
CODE = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*-[\w.-]*|\b\d+\.[A-Z]\b|\(\w\)")
NUMERIC_RANGE = re.compile(r"\b(\d+(?:\.\d+)?)-(?=\d)")
NAMED = re.compile(r"\b[A-Za-z_]+\d\w*")             # sensor and fleet names carry digits that are not quantities
WORD_HYPHEN = re.compile(r"(?<=[A-Za-z])-(?=\d)")     # Part-145 is not minus 145
LIST_MARK = re.compile(r"^\s*\d+[.)]\s", re.M)
ADVICE = re.compile(r"[^.!?\n]*(?:\b(?:I|we)\s+(?:would\s+)?(?:recommend|suggest|advise)\b"
                    r"|\byou should\s+(?:approve|reject|not|ignore|override|delay|keep|fly|remove|swap|replace|wait)\b"
                    r"|\b(?:instead of|rather than)\s+(?:the|this)\s+(?:plan|recommendation)\b)[^.!?\n]*[.!?]?", re.I)
MAX_MATCHES = 6


@dataclass
class Explanation:
    source: str                                   # "model" or "glossary"
    text: str = ""
    model: str | None = None
    provider: str | None = None
    note: str = ""
    unsupported: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    duration_s: float | None = None

    @property
    def label(self) -> str:
        if self.source != "model":
            return self.note
        via = "via Nightshift" if self.provider == "meterless" else f"via the {self.provider} fallback API"
        label = f"AI explanation by {self.model} {via}; it explains, it does not change the decision."
        if "nemotron" not in (self.model or "").lower():
            label += " This model is not Nemotron: the Nightshift lane is not pinned."
        return label


def _plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def brief_facts(case: dict | None, pack: dict) -> list[str]:
    """The brief as the screen shows it, one fact per line. This is all the model sees of the case and what its
    numbers are checked against: no raw evidence content, no sensor rows, no sign-off details."""
    if not case:
        return []
    th = pack.get("thresholds") or {}
    findings, rec = case.get("findings") or {}, case.get("recommendation") or {}
    asset = case.get("asset") or {}
    a, b = findings.get("rul_ml"), findings.get("rul_trend")
    rc = rul_context(findings, pack)
    facts = [f"Engine {asset.get('asset_id') or 'not resolved'}"
             + (f" ({asset['variant']} fleet)" if asset.get("variant") else "") + f". Question: {case.get('question')}",
             f"Verdict: {verdict_headline(case)} ({verdict_meta(case)}). Colour {TONE_COLOUR[verdict_tone(case)]}."]
    facts += [f"Degraded: {note}" for note in case.get("degraded") or []]
    if a:
        facts.append(f"Method A (machine learning) flights left: {fmt_rul(a, with_range=True)} (10th–90th percentile "
                     f"range), about {rc['a_days']:.0f} days; at least {rc['a_lower_days']:.0f} days at the 10th "
                     f"percentile. Evidence {a.get('evidence_id')}.")
    if b:
        facts.append(f"Method B (wear trend) flights left: {fmt_rul(b)}, about {rc['b_days']:.0f} days."
                     + (f" {b['note']}." if b.get("note") else "") + f" Evidence {b.get('evidence_id')}.")
    if "gap" in rc:
        facts.append(f"A and B are {rc['gap']:.0f} flights apart (limit {rc['divergence_limit']}): "
                     + ("they disagree." if rc["diverged"] else "they agree."))
    for mode in sorted(findings.get("fault_modes") or [], key=lambda m: -m["probability"]):
        facts.append(f"Fault mode {fault_label(mode['failure_mode'], pack)}: p = {mode['probability']:.2f}. "
                     f"Evidence {mode.get('evidence_id')}.")
    if findings.get("similar_cases") is not None:
        facts.append(f"Similar past cases: {len(findings.get('similar_cases') or [])}.")
    if rec:
        parts = rec.get("parts") or []
        facts.append("Plan: " + "; ".join(x for x in [
            rec.get("action"), f"deadline {rec['deadline']}" if rec.get("deadline") else "",
            f"hangar slot {rec['slot']}" if rec.get("slot") else "",
            f"tasks {', '.join(rec['task_ids'])}" if rec.get("task_ids") else "no task selected",
            ", ".join(f"part {p['part_number']} need {p['qty']}, {p['available']} on hand, {p['lead_time_days']} d lead"
                      for p in parts)] if x) + ".")
    graph = world_eligibility(case)
    if graph:
        facts.append(f"World Model: {len(graph['listed'])} eligible tasks for this fault "
                     f"({', '.join(graph['listed'])}); the plan uses {', '.join(graph['used']) or 'none'}. "
                     f"Evidence {graph['evidence_id']}.")
    for esc in case.get("escalations") or []:
        view = escalation_view(esc)
        facts.append(f"Escalation ({view['severity']}): {view['title']}. {view['message']}")
    for doc in findings.get("documents") or []:
        if doc.get("mandatory"):
            facts.append(f"Mandatory document {doc['doc_id']} {doc['section']} requires {doc.get('required_task')} "
                         f"within {doc.get('within_cycles')} cycles.")
    facts.append("Checks: " + "; ".join(text for text, _ in check_summary(case)) + ".")
    facts += [f"Why this plan: {_plain(line)}" for line in decision_boundaries(case, pack)]
    facts += [f"In plain words: {_plain(line)}" for line in plain_summary(case, pack)]
    facts.append(f"Planning rules: {th.get('cycles_per_day')} flights a day; remaining life capped at "
                 f"{th.get('rul_cap_cycles')} flights; the methods disagree above {th.get('rul_divergence_cycles')} "
                 f"flights; severity severe under {th.get('severe_rul_cycles')} flights, moderate under "
                 f"{th.get('moderate_rul_cycles')}.")
    return facts


def build_prompt(question: str, facts: list[str], entries: list[dict]) -> str:
    brief = "\n".join(f"- {fact}" for fact in facts) or "- No brief is open; explain terms only."
    terms = "\n".join(f"- {entry_line(e)}" for e in entries)
    return f"{PROMPT}\nBrief on screen:\n{brief}\n\nGlossary:\n{terms}\n\nQuestion: {question}\n"


def _numbers(text: str) -> list[tuple[float, float]]:
    """(value, rounding tolerance) of each quantity. List markers, dates, codes such as EV-PRG-01 and names such as
    T30 or FD001 are left out; dates and codes are checked whole instead."""
    text = CODE.sub(" ", DATE.sub(" ", LIST_MARK.sub(" ", text)))
    text = WORD_HYPHEN.sub(" ", NAMED.sub(" ", NUMERIC_RANGE.sub(r"\1 ", text)))
    return claim_numbers(text)


def unsupported_tokens(answer: str, grounding: str) -> list[str]:
    """Numbers, dates and identifiers in the answer that the brief facts and glossary given to the model do not
    contain. Same rounding rule as the brief's own number check: a whole number may sit 0.5 from the source."""
    known = grounding.lower()
    flagged = [d for d in DATE.findall(answer) if d not in grounding]
    for token in (t.rstrip(".-") for t in IDENTIFIER.findall(answer)):    # an ID can end a sentence
        if not token.startswith("(") and not re.fullmatch(r"[\d-]+", token) and token.lower() not in known:
            flagged.append(token)
    allowed = {value for value, _ in _numbers(grounding)}
    for value, tolerance in _numbers(answer):
        if not any(abs(value - c) <= max(tolerance, 0.005 * abs(c)) for c in allowed):
            flagged.append(f"{value:g}")
    return list(dict.fromkeys(flagged))


def advice_in(answer: str) -> list[str]:
    """Sentences that read as advice rather than explanation."""
    return [" ".join(m.group(0).split()) for m in ADVICE.finditer(answer)]


def _fallback(entries: list[dict], note: str) -> Explanation:
    return Explanation(source="glossary", note=note + " Matching glossary entries instead.",
                       entries=entries[:MAX_MATCHES])


def explain(question: str, case: dict | None, pack: dict, settings: Settings,
            client: LlmClient | None = None) -> Explanation:
    """Answer one question about the brief on screen. Tests pass a client on a mock Nightshift transport."""
    question = " ".join((question or "").split())[:500]
    if not question:
        return Explanation(source="glossary", note="Type a question first.")
    entries = glossary(pack)
    matches = search_glossary(question, entries)
    if settings.sample_mode:
        return _fallback(matches, "Sample mode: AI help is off and nothing leaves this machine.")
    facts = brief_facts(case, pack)
    own = client is None
    client = client or LlmClient(settings)
    try:
        if not client.configured:
            return _fallback(matches, "AI help needs Nightshift, and no Nightshift runtime token is set.")
        reply = client.chat([{"role": "user", "content": build_prompt(question, facts, entries)}],
                            model=settings.meterless_critic_model, temperature=0, max_tokens=600,
                            app=settings.meterless_critic_app, timeout_s=settings.critic_timeout_s)
    except (LlmUnavailable, LlmRefusal, httpx.HTTPError) as error:
        log.warning("explain fell back to the glossary (%s: %s)", type(error).__name__, str(error)[:200])
        return _fallback(matches, f"Nightshift did not answer ({type(error).__name__}).")
    finally:
        if own:
            client.close()
    text = reply.text.strip()
    if not text:
        log.warning("explain fell back to the glossary (empty answer from %s)", reply.model)
        return _fallback(matches, f"{reply.model} returned an empty answer.")
    grounding = "\n".join([*facts, *(entry_line(e) for e in entries)])
    result = Explanation(source="model", text=text, model=reply.model, provider=reply.provider,
                         unsupported=unsupported_tokens(text, grounding), advice=advice_in(text),
                         entries=matches[:MAX_MATCHES], duration_s=reply.duration_s)
    log.info("explain provider=%s requested=%s model=%s duration_s=%.2f unsupported=%d advice=%d", reply.provider,
             reply.requested_model, reply.model, reply.duration_s, len(result.unsupported), len(result.advice))
    return result
