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
from casefile.wear import NOT_DETECTED
from ui.brief import (check_summary, decision_boundaries, document_groups, escalation_view, fault_display, fault_label,
                      fmt_rul, rul_context, verdict_headline, verdict_meta, verdict_tone, world_eligibility)
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
TONE_COLOUR = {"urgent": "red (act now)", "warning": "amber (plan soon or inspect first)",
               "ok": "green (no action proposed before the next scheduled inspection)", "hold": "grey (no recommendation)"}
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# IDENTIFIER without its numeric-range branch, so "5-11" still yields 5 and 11 while SB-TF-72-0142 yields nothing.
CODE = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*-[\w.-]*|\b\d+\.[A-Z]\b|\(\w\)")
NUMERIC_RANGE = re.compile(r"\b(\d+(?:\.\d+)?)-(?=\d)")
NAMED = re.compile(r"\b[A-Za-z_]+\d\w*")             # sensor and fleet names carry digits that are not quantities
WORD_HYPHEN = re.compile(r"(?<=[A-Za-z])-(?=\d)")     # Part-145 is not minus 145
LIST_MARK = re.compile(r"^\s*\d+[.)]\s", re.M)
DECISION_VERBS = r"(?:approve|reject|ignore|override|delay|defer|keep|fly|remove|swap|replace|wait|ground|dispatch)"
ADVICE = re.compile(r"[^.!?\n]*(?:\b(?:I|we)\s+(?:would\s+)?(?:recommend|suggest|advise)\b"
                    rf"|\byou should\s+(?:not\s+)?{DECISION_VERBS}\b"
                    rf"|\b(?:must|should|need to|needs to|has to|have to|ought to|best to|better to)\s+(?:not\s+)?"
                    rf"{DECISION_VERBS}\b"
                    r"|\b(?:instead of|rather than)\s+(?:the|this)\s+(?:plan|recommendation)\b)[^.!?\n]*[.!?]?", re.I)
# An order at the start of a sentence: "Approve it." "Ground the engine now."
IMPERATIVE = re.compile(r"(?:^|(?<=[.!?]))\s*((?:approve|reject|ignore|override|defer|remove|swap|ground|dispatch)\b"
                        r"[^.!?\n]*[.!?]?)", re.I | re.M)
# Numbers written as words ("about forty flights", "a dozen"); ordinals are left out ("the first free slot").
NUMBER_WORD = re.compile(r"\b(?:(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](one|two|three|four|"
                         r"five|six|seven|eight|nine))?|(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
                         r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|dozen|hundred|"
                         r"thousand))\b", re.I)
UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
UNITS.update({"dozen": 12, "hundred": 100, "thousand": 1000})
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
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
        label = f"AI explanation by {self.model} via Nightshift; it explains, it does not change the decision."
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
    fault, note = fault_display(case, pack)
    if fault == NOT_DETECTED:       # one-mode fleet, healthy engine: the 1.00 is by construction
        modes = findings.get("fault_modes") or []
        facts.append(f"Fault mode: {fault} ({note}). Evidence {modes[0].get('evidence_id')}.")
    else:
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
    for key, title, docs in document_groups(case):
        facts.append(f"Documents, {title.lower()}: " + "; ".join(
            f"{d['doc_id']} {d['section']}" + (f" ({d['group_reason']})" if d.get("group_reason") else "")
            for d in docs) + ".")
    facts += [f"Method of compliance: {c['text']}." for c in rec.get("compliance") or []]
    package = rec.get("work_package") or {}
    if package:
        facts.append("Work package (synthetic task references): " + "; ".join(
            f"{s['title']} {s['when']}, {len(s['tasks'])} tasks, {s['manhours']:g} manhours"
            for s in package.get("sections") or []) + f"; {package['manhours']:g} manhours and {package['rii']} RII "
            "in all.")
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
    allowed = {value for value, _ in _numbers(grounding)} | {value for _, value in _word_numbers(grounding)}
    for value, tolerance in _numbers(answer):
        if not any(abs(value - c) <= max(tolerance, 0.005 * abs(c)) for c in allowed):
            flagged.append(f"{value:g}")
    for word, value in _word_numbers(answer):
        if not any(abs(value - c) <= 0.5 for c in allowed):
            flagged.append(word)
    return list(dict.fromkeys(flagged))


def _word_numbers(text: str) -> list[tuple[str, float]]:
    """(words, value) of each number written in words: "forty", "thirty-one", "a dozen"."""
    out = []
    for match in NUMBER_WORD.finditer(text):
        tens, unit, alone = (g.lower() if g else None for g in match.groups())
        value = TENS[tens] + (UNITS[unit] if unit else 0) if tens else UNITS[alone]
        out.append((match.group(0), float(value)))
    return out


def advice_in(answer: str) -> list[str]:
    """Sentences that read as advice rather than explanation: a recommendation, a "must/should" with a decision verb,
    or an order at the start of a sentence ("Approve it.")."""
    found = [" ".join(m.group(0).split()) for m in ADVICE.finditer(answer)]
    found += [" ".join(m.group(1).split()) for m in IMPERATIVE.finditer(answer)]
    return list(dict.fromkeys(f for f in found if f))


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
        app, model = settings.lane("explain")
        reply = client.chat([{"role": "user", "content": build_prompt(question, facts, entries)}],
                            model=model, temperature=0, max_tokens=600, app=app, timeout_s=settings.critic_timeout_s)
    except (LlmUnavailable, LlmRefusal, httpx.HTTPError) as error:
        log.warning("explain fell back to the glossary (%s: %s)", type(error).__name__, str(error)[:200])
        return _fallback(matches, f"Nightshift did not answer ({type(error).__name__}).")
    finally:
        if own:
            client.close()
    from tools.status import note_model_reply
    note_model_reply(settings.lane("explain")[0], reply.model)       # the status strip names the lane's model
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
