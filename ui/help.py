"""Plain-language help for engineers and managers who are not data scientists: the section notes, the glossary,
the hover tooltips on abbreviations, the screen guide and the help panel's step strip.
Every number in a sentence about this engine comes from the case file; this module only words it."""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from casefile.schemas import AGENT_CODES, INSPECTION_CODE
from casefile.wear import NOT_DETECTED
from ui.brief import (ESCALATION_TITLES, check_summary, fault_display, fault_label, primary_fault, rul_context,
                      verdict_tone, world_eligibility)

GLOSSARY_FILE = Path(__file__).with_name("glossary.yaml")

DISCLAIMER = ("Hackathon prototype. Runs on NASA C-MAPSS simulated telemetry and synthetic maintenance, spares and "
              "document data. Not certified or approved by any aviation authority and not for operational use. It "
              "does not issue a return to service or a certificate of release to service.")
DISCLAIMER_SHORT = "Prototype on simulated data: not for operational use; it never issues a release to service."
INTENDED_USE = ("Advisory decision support. Never the sole source of an airworthiness determination or a change to "
                "maintenance intervals, and never a release to service; certifying staff release in the "
                "organisation's own system.")
CLEARANCE = ("Clearance here means the engineer's decision to keep, inspect or remove an engine. The release to "
             "service stays with certifying staff.")
WHAT_IT_DOES = ("Cleris answers three questions about one engine: what is wrong, how long it can keep flying, and what "
                "to do. Specialist agents each look at one thing (sensor data, remaining life, fault type, documents, "
                "parts and hangar slots). Code checks and a reviewer then try to find mistakes before you see the "
                "result. Every number is calculated by code, not written by the AI, every statement links to its "
                "evidence, and nothing is drafted until you sign.")

SECTION_HELP = {
    "verdict": "The coloured box is the recommendation. Red means act now, amber means plan soon or check "
               "something first, green means no action is proposed before the next scheduled inspection.",
    "metrics": "Two independent methods estimate how many more flights the engine can make before it should "
               "come off the wing. If they disagree a lot, the tool lowers its confidence and asks for an inspection "
               "instead of averaging them.",
    "chart": "Each line is one engine sensor, shown as how far it has drifted from where this engine started "
             "(0 = as new). Lines climbing away from 0 show wear building up.",
    "plan": "What to do, by when, and with which parts. Every statement ends with evidence tags (EV-…) that "
            "point to the data behind it.",
    "context": "Background on this engine: its maintenance record, which sensors changed most, and how much "
               "it is scheduled to fly.",
    "decision": "Before you see the brief, automatic checks and a reviewer try to find mistakes. If the reviewer "
                "finds a problem, the plan is revised (up to 3 rounds). In sample mode nothing leaves this machine. "
                "In live mode the evidence summaries go to the Nemotron critic through Nightshift before you approve "
                "(and a question the parser cannot read goes to Nemotron intake); no work order is drafted until "
                "you approve.",
    "evidence": "The raw facts behind the brief. Open any item to see exactly what was measured or calculated "
                "and where it came from.",
    "inspection": "When the brief asks for an inspection, the result comes back here. Enter what the inspector "
                  "found; code compares a measurement with the manual's limit (synthetic in this demo). Beyond "
                  "limits, the plan removes the engine before further flight; at the limit, it re-inspects within the "
                  "manual's interval; within limits, no removal. Findings are stored for the engine and never edited.",
    "signoff": "You stay in charge. Type your name (required) and tick each urgent, warning or mandatory-document "
               "escalation as read (info notes need no tick; the log records them as noted); with an urgent "
               "escalation, also say why you approve despite it. Approve then drafts a work order for your "
               "organisation's own release process. It is not a certificate of release to service. Reject drafts "
               "nothing and records your reason. Both go into the decision log, which records agent, gate, model and "
               "engineer decisions with a hash chain. The tool never creates work orders on its own.",
}

HOW_TO_READ = [
    "Read the coloured box first: it is the recommendation and its deadline. The chips under it say how the brief "
    "was checked (rule checks, critic rounds, the model that answered).",
    "Read **In plain words** for a one-paragraph explanation.",
    "Check the numbers row: remaining flights by two methods, and which part is wearing.",
    "Look at **Plan** for the task, parts and hangar slot, and **Why this plan and not another** for the rules "
    "that chose it.",
    "If you want proof, open any **EV-** item under Details. The **Decision log** tab lists who decided what: "
    "agents, gates, the model and, once you sign, you.",
    "Type your name in **Signed by** and tick each urgent, warning or mandatory-document escalation as read (info "
    "notes need no tick; they are recorded as noted); with an urgent escalation, also say why you "
    "approve despite it. **Approve** drafts a work order for your organisation's own release process; it is not a "
    "certificate of release to service. **Reject** records your comment and drafts nothing. Either way the "
    "decision log is stored with a hash chain.",
]

TONE_WORDS = {
    "urgent": "**Red**: act now; the engine may wear out before the normal repair can happen.",
    "warning": "**Amber**: plan soon, or inspect first because the evidence is uncertain.",
    "ok": "**Green**: no action proposed before the next scheduled inspection.",
    "hold": "**Grey**: no recommendation (question unclear, or not enough evidence).",
}

# From an arriving aircraft to a signed draft work order: the step strip in the help panel.
FLOW_STEPS = [
    ("Aircraft arrives", "The engine's latest flights are recorded."),
    ("Engineer asks", "A question names the engine, e.g. FD001-A031."),
    ("Evidence read in place", "Zetaris reads sensor data, maintenance history, spares, slots and documents "
                               "where they live, read-only."),
    ("Diagnose and forecast", "Two methods estimate the flights left, a matcher finds the worn part, and H-MEM "
                              "finds similar past removals."),
    ("Plan", "The World Model lists the tasks allowed for the fault; the planner picks by deadline, parts and "
             "hangar slot."),
    ("Gates check the brief", "Rule checks, then the critic (Nemotron via Nightshift in live mode), then the "
                              "escalation rules."),
    ("Engineer signs", "Approve drafts a work order with its decision log; Reject records why."),
]

# Illustrative photos for the step strip, from the asset pack's Wikimedia Commons set (credited where shown).
KEYFRAMES = [
    ("engine_core.jpg", "Fan cowl open: the core whose sensors the brief reads (steps 3 and 4)."),
    ("fan_inspection.jpg", "Blades out for inspection: where an Inspect verdict leads."),
    ("inspection.jpg", "Engineers at the engine: the decision and the work order (step 7) land here."),
]
KEYFRAME_NOTE = ("Illustrative photos of the same engine class, not the monitored engine. The brief itself runs on "
                 "NASA C-MAPSS simulated engine data.")

# Help topics a section header can open, with the glossary terms that section uses.
TOPICS = {
    "verdict": ("The verdict", "verdict", ["Confidence", "Deadline", "Planning bound", "RUL", "Percentile and range",
                                          "Divergence", "Escalation", "Rule checks", "Critic"]),
    "plan": ("The plan", "plan", ["Task ID", "FMEA", "World Model", "Severity", "Planning bound", "Lead time",
                                  "Hangar slot", "Lease engine swap", "Shop visit"]),
    "signoff": ("Your decision", "signoff", ["Sign-off", "Escalation", "Work order (draft)",
                                             "Certificate of release to service", "Decision log", "Hash chain"]),
    "details": ("The detail tabs", "evidence", ["Evidence ID", "Claim", "Rule checks", "Critic", "Decision log",
                                                "Hash chain", "Trace"]),
    "inspection": ("Inspection findings", "inspection", ["Inspection finding", "Limit section", "Serviceable limit",
                                                         "Reject limit", "Inspector-classified", "Borescope",
                                                         "Potentially reportable"]),
}

AGENT_NAMES = {"intake": "intake", "planner": "planner", "data": "data agent", "prognostics": "prognostics",
               "diagnostics": "diagnostics", "knowledge": "knowledge (documents)",
               "maint_planner": "maintenance planner", "rule_checks": "rule checks", "critic": "critic",
               "escalation": "escalation rules"}
EVIDENCE_REF = re.compile(r"\bEV-([A-Z]{3})-\d{2,}\b")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


@lru_cache
def _read_glossary(path: Path = GLOSSARY_FILE) -> tuple[dict, ...]:
    entries = []
    for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        entries.append({"term": _clean(raw.get("term")), "full": _clean(raw.get("full")),
                        "meaning": _clean(raw.get("meaning")), "where": _clean(raw.get("where")),
                        "aliases": [_clean(a) for a in raw.get("aliases") or []],
                        "abbr": [_clean(a) for a in raw.get("abbr") or []]})
    return tuple(entries)


GLOSSARY: list[dict] = list(_read_glossary())


def sensor_entries(pack: dict) -> list[dict]:
    """One glossary entry per sensor in the asset pack, worded from its description, unit and fault directions."""
    modes = list((pack.get("failure_modes") or {}))
    components = pack.get("components") or {}
    out = []
    for sensor, spec in (pack.get("sensors") or {}).items():
        directions = spec.get("direction") or {}
        moves = {directions.get(m, "none") for m in modes}
        if moves == {"none"}:
            how = "It does not change with wear in this data, so the models do not use it."
        elif len(moves) == 1:
            how = f"It {'rises' if moves == {'up'} else 'falls'} as the engine wears, whichever part is wearing."
        else:
            how = ("It moves " + " but ".join(f"{directions.get(m)} for {fault_label(m, pack)}" for m in modes)
                   + ", so it tells the faults apart.")
        part = components.get(spec.get("component"), spec.get("component") or "engine")
        what = spec.get("description") or spec["name"]
        out.append({"term": spec["name"], "full": f"sensor {sensor}",
                    "meaning": f"Sensor on the {part.lower()}: {what[0].lower() + what[1:]} ({spec.get('unit')}). "
                               f"{how}",
                    "where": "Engine diagram, Fault tab sensor trends, Remaining life drivers.",
                    "aliases": [sensor], "abbr": []})
    return out


def glossary(pack: dict | None = None) -> list[dict]:
    """The glossary file plus one entry per sensor of the loaded asset pack."""
    return [*GLOSSARY, *(sensor_entries(pack) if pack else [])]


def entry_line(entry: dict) -> str:
    """One line per term, as the search shows it and as the AI help is given it."""
    name = entry["term"] + (f" ({entry['full']})" if entry["full"] else "")
    return f"{name}: {entry['meaning']}"


def _normal(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9%]+", " ", text.lower()) + " "


def terms_in(text: str, entries: list[dict]) -> list[dict]:
    """Entries whose term, expansion or alias the text names as whole words, in glossary order."""
    words = _normal(text)
    found = []
    for entry in entries:
        names = [entry["term"], entry["full"], *entry["aliases"]]
        if any(name and _normal(name) in words for name in names):
            found.append(entry)
    return found


def search_glossary(query: str, entries: list[dict]) -> list[dict]:
    """For the search box and the AI-help fallback: terms the query names first, then names that start with it and
    meanings that contain it as a word, then entries sharing a longer word with it."""
    q = " ".join((query or "").split()).lower()
    if not q:
        return list(entries)
    named = terms_in(q, entries)
    starts, word = re.compile(r"\b" + re.escape(q)), re.compile(r"\b" + re.escape(q) + r"\b")
    rest = [e for e in entries if e not in named and (
        any(starts.search(n.lower()) for n in [e["term"], e["full"], *e["aliases"]]) or word.search(e["meaning"].lower()))]
    if named or rest:
        return named + rest
    words = set(re.findall(r"[a-z0-9]{5,}", q))
    return [e for e in entries if any(w in " ".join([e["term"], *e["aliases"], e["meaning"]]).lower() for w in words)]


def topic_entries(topic: str, entries: list[dict]) -> list[dict]:
    terms = TOPICS.get(topic, ("", "", []))[2]
    by_term = {e["term"]: e for e in entries}
    return [by_term[t] for t in terms if t in by_term]


def evidence_tip(evidence_id: str) -> str:
    code = EVIDENCE_REF.match(evidence_id)
    agent = next((a for a, c in AGENT_CODES.items() if code and c == code.group(1)), None)
    who = f"produced by the {AGENT_NAMES.get(agent, agent)}" if agent else "an evidence record"
    if code and code.group(1) == INSPECTION_CODE:
        who = "an inspection finding an inspector entered, recorded by the data agent"
    return f"Evidence ID: {who}. Open it under Details, Checks and evidence."


@lru_cache
def _abbr_pattern(tokens: tuple[str, ...]) -> re.Pattern:
    names = "|".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<![\w-])(EV-[A-Z]{3}-\d{2,}" + (f"|{names}" if names else "") + r")(?!\w)")


def tooltip(text: Any, entries: list[dict]) -> str:
    """HTML-escape `text` and give each glossary abbreviation and evidence ID in it a hover title. On a touch screen
    a title does not show, so the Help panel stays the main route."""
    text = str(text if text is not None else "")
    tips = {a: entry_line(e) for e in entries for a in e["abbr"]}
    pattern = _abbr_pattern(tuple(sorted(tips, key=len, reverse=True)))
    out, last = [], 0
    for match in pattern.finditer(text):
        token = match.group(0)
        title = evidence_tip(token) if token.startswith("EV-") else tips[token]
        out += [html.escape(text[last:match.start()], quote=False),
                f'<abbr title="{html.escape(title)}">{html.escape(token)}</abbr>']
        last = match.end()
    out.append(html.escape(text[last:], quote=False))
    return "".join(out)


def bold_html(text: str) -> str:
    """Escape text and turn **bold** into <b>, for sentences built here and shown as HTML."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text, quote=False))


def glossary_html(entries: list[dict]) -> str:
    esc = html.escape
    return '<div class="gloss">' + "".join(
        f'<div><b>{esc(e["term"])}</b>' + (f' <span class="full">· {esc(e["full"])}</span>' if e["full"] else "")
        + f'<div>{esc(e["meaning"])}</div>'
        + (f'<div class="where">Where: {esc(e["where"])}</div>' if e["where"] else "") + "</div>"
        for e in entries) + "</div>"


def guide_html(rows: list[tuple[str, str]]) -> str:
    return '<dl class="guide">' + "".join(f"<dt>{html.escape(title)}</dt><dd>{bold_html(text)}</dd>"
                                          for title, text in rows) + "</dl>"


def flow_html(steps: list[tuple[str, str]] = FLOW_STEPS) -> str:
    return '<div class="flow">' + "".join(
        f'<div class="flow-step"><span class="flow-n">{n}</span><b>{html.escape(title)}</b>{html.escape(text)}</div>'
        for n, (title, text) in enumerate(steps, 1)) + "</div>"


def _days(cycles: float, per_day: float) -> str:
    days = cycles / per_day
    return "less than a day" if days < 1 else f"about {days:.0f} day{'s' if round(days) != 1 else ''}"


def plain_summary(case: dict, pack: dict) -> list[str]:
    """A few short sentences in everyday language, built only from values already in the case file."""
    findings = case.get("findings") or {}
    rec = case.get("recommendation") or {}
    asset = (case.get("asset") or {}).get("asset_id") or "This engine"
    per_day = pack.get("thresholds", {}).get("cycles_per_day") or 1
    status = case.get("status")
    lines: list[str] = []

    if status == "needs_clarification":
        return ["The question did not name an engine the tool could find. Try an engine ID such as FD001-A001."]
    if status == "insufficient_evidence":
        return [f"The tool could not build a brief for {asset} that passed its own checks, so it stopped rather "
                "than guess. The gaps it found are listed below."]
    if status == "error":
        return ["The run failed before a recommendation was made. See the Trace tab for the step that failed."]

    a, b = findings.get("rul_ml"), findings.get("rul_trend")
    modes = sorted(findings.get("fault_modes") or [], key=lambda m: -m["probability"])
    healthy = rul_context(findings, pack)["healthy"]

    if healthy:
        lines.append(f"{asset} shows no clear sign of wear yet; its health score is still in the range of new engines.")
    elif modes:
        top = modes[0]
        info = pack.get("failure_modes", {}).get(top["failure_mode"], {})
        part = pack.get("components", {}).get(info.get("component"), info.get("component", "engine"))
        certainty = "very likely" if top["probability"] >= 0.9 else "likely" if top["probability"] >= 0.6 else "possibly"
        lines.append(f"{asset} is wearing out, {certainty} in the **{part.lower()}** "
                     f"({info.get('label', top['failure_mode'])}).")

    current = [i for i in findings.get("inspections") or [] if i.get("current", True)]
    if current:
        worst = max(current, key=lambda i: ["within", "at_limit", "beyond"].index(i["result"]))
        spec = pack.get("inspections") or {}
        kind = (spec.get("kinds") or {}).get(worst["kind"], worst["kind"]).split(" (")[0].lower()
        module = (spec.get("modules") or {}).get(worst["module"], worst["module"])
        result = {"within": "within limits", "at_limit": "at the limit", "beyond": "beyond limits"}[worst["result"]]
        then = {"beyond": "so the engine comes off before it flies again",
                "at_limit": f"so it is looked at again within {worst.get('interval_cycles')} flights",
                "within": "so that finding needs no removal"}[worst["result"]]
        lines.append(f"An inspector's **{kind}** of the {module} found it **{result}**, {then}.")

    if a and b and not healthy:
        low, high = sorted([b["rul"], a["rul"]])
        lines.append(f"It can fly roughly **{low:.0f}–{high:.0f} more flights** (lower and higher of two methods), "
                     f"which is {_days(low, per_day)} to {_days(high, per_day)} at {per_day} flights a day.")
    elif a:
        lines.append(f"It has an estimated **{a['rul']:.0f} or more flights** left "
                     f"({_days(a['rul'], per_day)} at {per_day} flights a day).")

    short = [p for p in rec.get("parts") or [] if p["available"] < p["qty"]]
    if short:
        worst = max(short, key=lambda p: p["lead_time_days"])
        lines.append(f"A needed part ({worst['part_number']}) is out of stock and takes "
                     f"**{worst['lead_time_days']} days** to arrive.")

    for esc in case.get("escalations") or []:
        if esc.get("rule") == "rul_below_lead_time":
            swap = "swap" in (rec.get("action") or "").lower()
            lines.append("The engine will wear out sooner than parts and a hangar slot can be arranged for a normal "
                         "repair" + (", so a rented spare engine keeps the aircraft flying meanwhile." if swap else "."))
        elif esc.get("rule") == "rul_divergence":
            lines.append("The two life estimates disagree, so an inspection is suggested before committing.")
        elif esc.get("rule") == "ambiguous_fault":
            lines.append("It is not clear which part is wearing, so an inspection of both is suggested.")

    for doc in (findings.get("documents") or []):
        if doc.get("mandatory"):
            kind = doc.get("doc_type", "document").replace("_", " ")
            lines.insert(1, f"An **{kind}** ({doc['doc_id']}) applies to this engine and **requires**: "
                            f"{(doc.get('action') or doc.get('required_task') or '').rstrip('.')}. "
                            "The tool cannot drop this from the plan.")

    if rec.get("action"):
        when = f" by **{rec['deadline']}**" if rec.get("deadline") else ""
        lines.append(f"Recommendation: **{rec['action'].rstrip('.')}**{when}.")

    verdicts = case.get("verdicts") or []
    revisions = sum(1 for v in verdicts if v.get("gate") == "critic" and v.get("outcome") == "revise")
    similar = len(findings.get("similar_cases") or [])
    checks = "Automatic checks and a reviewer approved this brief"
    checks += f" after sending it back {revisions} time{'s' if revisions != 1 else ''}" if revisions else ""
    checks += f"; {similar} similar past engine{'s' if similar != 1 else ''} had the same problem." if similar else "."
    lines.append(checks)
    return lines


def planning_bound(case: dict) -> float | None:
    """The planner's own remaining-life lower bound (the deadline comes from it), else the lower of A's and B's lows."""
    for item in (case.get("evidence") or {}).values():
        content = item.get("content") or {}
        if item.get("produced_by") == "maint_planner" and "rul_lower_cycles" in content:
            return content["rul_lower_cycles"]
    findings = case.get("findings") or {}
    lows = [f["lower"] for f in (findings.get("rul_ml"), findings.get("rul_trend")) if f]
    return min(lows) if lows else None


def screen_guide(case: dict | None, pack: dict) -> list[tuple[str, str]]:
    """What each part of the brief tells you, top to bottom, worded from the open case where there is one."""
    case = case or {}
    th = pack.get("thresholds") or {}
    cpd, limit = th.get("cycles_per_day", 2), th.get("rul_divergence_cycles", 20)
    findings, rec = case.get("findings") or {}, case.get("recommendation") or {}
    a, b = findings.get("rul_ml"), findings.get("rul_trend")
    asset = (case.get("asset") or {}).get("asset_id") or "this engine"
    rows = []

    if rec.get("action"):
        verdict = f"The coloured box says what to do with {asset}: **{rec['action']}**. {TONE_WORDS[verdict_tone(case)]}"
    elif case:
        verdict = f"There is no recommendation for this question. {TONE_WORDS['hold']}"
    else:
        verdict = "The coloured box at the top is the recommendation. " + " ".join(TONE_WORDS.values())
    rows.append(("Verdict", verdict))

    bound = planning_bound(case) if case else None
    if rec.get("deadline") and bound is not None:
        rows.append(("Deadline and confidence",
                     f"Do it by **{rec['deadline']}**. The date comes from the planning bound, the cautious end of the "
                     f"remaining-life estimates (**{bound:.0f} flights**, at {cpd} flights a day), never from the "
                     f"middle or an average. Confidence is **{rec.get('confidence')}**; it drops to low when the two "
                     "methods disagree or the input data failed a check."))
    else:
        rows.append(("Deadline and confidence",
                     "The deadline is the last date for the work. It comes from the cautious end of the remaining-life "
                     f"estimates at {cpd} flights a day. Confidence (high, medium or low) drops when the two methods "
                     "disagree or the input data failed a check."))

    if a and b:
        rc = rul_context(findings, pack)
        rows.append(("Two remaining-life numbers",
                     f"Method A, learned from past engines, says **{a['rul']:.0f} flights** left; 8 in 10 engines like "
                     f"this one had between {a['lower']:.0f} and {a['upper']:.0f}. Method B, this engine's own wear "
                     f"trend, says **{b['rul']:.0f}**" + (" (the cap: no wear trend yet)" if b.get("at_cap") else "")
                     + f". They are {rc['gap']:.0f} flights apart, "
                     + (f"more than {limit}, so Cleris lowers its confidence and asks for an inspection instead of "
                        "averaging." if rc["diverged"] else f"within the limit of {limit}, so they agree well enough.")))
    else:
        rows.append(("Two remaining-life numbers",
                     "Two methods that work in different ways estimate how many more flights the engine can make. "
                     "Method A shows a range (8 in 10 similar engines fall inside it). If the two disagree by more "
                     f"than {limit} flights, Cleris lowers its confidence and asks for an inspection; it never "
                     "averages them."))

    fault, prob = primary_fault(findings, pack)
    shown, note = fault_display(case, pack)
    if prob is not None and shown == NOT_DETECTED:
        rows.append(("Fault mode", f"**No wear detected**; this fleet's only known wear mode is "
                                   f"{note.split(': ', 1)[-1]}. The two remaining-life methods agree and sit at or near "
                                   "the cap, and the health index is not above the fleet, so there is no fault to name "
                                   "yet; similar past cases are not searched."))
    elif prob is not None:
        modes = (pack.get("variants") or {}).get((case.get("asset") or {}).get("variant") or "", [])
        text = f"**{fault}**, probability {prob:.2f} (1.00 means the sensors point clearly to it). "
        if len(modes) > 1:
            text += f"This fleet can wear in {len(modes)} ways (" + " or ".join(fault_label(m, pack) for m in modes) + ")."
        elif modes:
            text += "This fleet has one known way to wear, so the probability is always 1.00."
        if rul_context(findings, pack)["healthy"]:
            text += " The engine shows no clear wear yet, so this is the part that would wear, not a finding."
        similar = len(findings.get("similar_cases") or [])
        if similar:
            text += f" {similar} similar past removals from case memory (H-MEM) are cited."
        rows.append(("Fault mode", text))
    else:
        rows.append(("Fault mode", "Which part of the engine is wearing (the HPC or the fan), with its probability."))

    if rec:
        graph = world_eligibility(case)
        parts = rec.get("parts") or []
        short = [p for p in parts if p["available"] < p["qty"]]
        bits = [f"tasks {', '.join(rec['task_ids'])} from the failure-mode table" if rec.get("task_ids") else
                "no task selected"]
        if graph:
            bits.append(f"the World Model allows {len(graph['listed'])} task{'s' if len(graph['listed']) != 1 else ''}"
                        " for this fault")
        if parts:
            bits.append(f"{len(parts) - len(short)} of {len(parts)} parts on hand")
        if rec.get("slot"):
            bits.append(f"first hangar slot {rec['slot']}")
        rows.append(("Plan", "The plan card lists the work: " + "; ".join(bits) + ". **Why this plan and not "
                     "another** gives each rule the planner applied and the value at which the plan would change."))
    else:
        rows.append(("Plan", "The plan card lists the tasks, parts and hangar slot. **Why this plan and not another** "
                             "gives each rule the planner applied and the value at which the plan would change."))

    found = [i for i in findings.get("inspections") or [] if i.get("current", True)]
    if found:
        rows.append(("Inspection findings",
                     f"{len(found)} inspection finding{'s' if len(found) != 1 else ''} an inspector entered "
                     f"({', '.join(i['evidence_id'] for i in found)}). Code compared each measurement with the "
                     "manual's limit section (synthetic here); a result with no limit is marked inspector-classified. "
                     "Beyond limits: remove before further flight; at the limit: re-inspect within the manual's "
                     "interval; within limits: no removal."))

    escalations = case.get("escalations") or []
    if escalations:
        titles = "; ".join(ESCALATION_TITLES.get(e.get("rule"), (e.get("rule") or "").replace("_", " "))
                           for e in escalations)
        rows.append(("Escalations", f"{len(escalations)} fixed rule{'s' if len(escalations) != 1 else ''} raised the "
                                    f"alarm: {titles}. Red cards are urgent, amber are warnings, blue-grey are notes. "
                                    "Tick each urgent, warning or mandatory-document card as read before Approve "
                                    "works (an urgent one also needs a reason); info notes need no tick and the "
                                    "decision log records them as noted."))
    else:
        rows.append(("Escalations", ("No escalation rule fired for this engine. " if case else "")
                     + "Red or amber cards under the verdict are fixed rules that raise the alarm; you tick each one as "
                       "read before approving (an urgent one also needs a reason). Blue-grey info notes need no tick; "
                       "they are recorded as noted."))

    evidence = case.get("evidence") or {}
    example = next(iter(sorted(evidence)), "EV-PRG-01")
    codes = ", ".join(f"{AGENT_CODES[agent]} {AGENT_NAMES[agent]}" for agent in
                      ("intake", "data", "prognostics", "diagnostics", "knowledge", "maint_planner"))
    rows.append(("Evidence tags", f"Tags like **{example}** point to the record behind a statement. The three letters "
                                  f"name the agent that made it ({codes}; INS is an inspection finding an "
                                  "inspector entered)."
                 + (f" This brief has {len(evidence)} records, under Details, Checks and evidence." if evidence else "")))

    chips = [text for text, _ in check_summary(case)] if case else []
    rows.append(("Checks", "The chips under the verdict say how the brief was checked"
                 + (f": {'; '.join(chips)}." if chips else ".")
                 + " Rule checks are code. The critic tries to disprove the brief; in live mode Nemotron adds "
                   "challenges that code confirms or refutes, and no model ever writes a number in the brief."))
    rows.append(("Decision log", "The last Details tab lists who decided what, in order: agents proposed, gates "
                                 "accepted or sent the work back, the model challenged, rules escalated, and you sign. "
                                 "When you approve or reject, the log is stored with a hash chain, so a later edit, "
                                 "deletion or reordering shows."))
    urgent = any(e.get("severity") == "urgent" for e in escalations)
    rows.append(("Sign-off", "Type your name in **Signed by** (required; typed, not checked against a licence "
                             "register) and tick each urgent, warning or mandatory-document escalation as read (info "
                             "notes need no tick; they are recorded as noted)"
                 + ("; because an escalation here is urgent, Approve also needs a short reason why you approve "
                    "despite it" if urgent else "; with an urgent escalation, Approve also needs a short reason")
                 + ". **Approve** drafts a work order for your organisation's own release process; it is not a "
                   "certificate of release to service. **Reject** drafts nothing and records your comment."))
    return rows
