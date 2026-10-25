"""Plain-language explanations for engineers and managers who are not data scientists.
Every number in the summary comes from the case file; this module only words it."""

from __future__ import annotations

from typing import Any

from ui.brief import rul_context

SECTION_HELP = {
    "verdict": "The coloured box is the recommendation. Red means act now, amber means plan soon or check "
               "something first, green means keep flying with routine checks.",
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
                "finds a problem, the plan is revised (up to 3 rounds). Nothing is sent anywhere until you approve.",
    "evidence": "The raw facts behind the brief. Open any item to see exactly what was measured or calculated "
                "and where it came from.",
    "signoff": "You stay in charge. Approving drafts a work order; rejecting records your reason. The tool never "
               "creates work orders on its own.",
}

GLOSSARY: list[tuple[str, str]] = [
    ("Cycle", "One flight: take-off, cruise and landing. Engine wear is counted in cycles."),
    ("RUL (remaining useful life)", "How many more cycles the engine can fly before it should be removed. "
     "Capped at 125: beyond that the data cannot tell the difference."),
    ("RUL A (machine learning)", "Learned from 160 past engines that ran until failure. Looks at the last 30 "
     "flights. Gives a range: the 10th–90th percentile means 8 out of 10 similar engines fall inside it."),
    ("RUL B (trend)", "Combines the sensors that worsen with wear into one health score and projects when it "
     "reaches the failure level. Simple and accurate near the end of life, less so early on."),
    ("Divergence (A vs B gap)", "If the two methods differ by more than 20 cycles, the tool does not trust "
     "either alone and recommends a borescope inspection."),
    ("Health index", "One number for overall wear: 0 is as new, higher is more worn. Compared with the rest of the "
     "fleet at the same age to spot engines wearing unusually fast."),
    ("Fault mode", "Which part of the engine is wearing: the fan (front) or the high-pressure compressor (HPC, "
     "core). Found by checking sensors that move in opposite directions for the two faults."),
    ("SHAP driver", "The sensors that most pushed RUL A down. They show how worn the engine is, but not always "
     "which part is worn."),
    ("Signature sensors", "Sensors that move one way for a fan fault and the other way for an HPC fault, so they "
     "tell the two apart."),
    ("Evidence ID (EV-…)", "A tag on every fact. The letters say which agent produced it (DAT data, PRG "
     "prognostics, DIA diagnostics, MNT maintenance planner, INT intake)."),
    ("Rule checks", "Automatic checks with no AI: every claim has evidence, numbers are in valid ranges, parts and "
     "slots exist."),
    ("Critic", "A reviewer that tries to disprove the brief and sends it back if something is wrong."),
    ("Escalation", "A rule that raises the alarm, e.g. when the engine will wear out before replacement parts can "
     "arrive."),
    ("Lead time", "Days between ordering a part and receiving it."),
    ("Hangar slot", "A free maintenance bay on a given day (2 per day in this data)."),
    ("Lease engine swap", "Fit a rented spare engine so the aircraft keeps flying while the worn engine waits for "
     "parts."),
    ("Borescope", "A camera pushed inside the engine to look at blades without taking it apart."),
    ("Airworthiness directive (AD)", "A legally binding order from the aviation authority. Its required actions "
     "must be done; the tool shows them first and cannot remove them."),
    ("Service bulletin (SB)", "A manufacturer's notice recommending an inspection or change. It outranks the manual "
     "when the two say different things."),
    ("Maintenance manual", "The standard procedures and limits for the engine. Only the revision in effect is used."),
    ("Document precedence", "When documents disagree: airworthiness directive, then service bulletin, then manual, "
     "then internal procedure, then past cases."),
    ("Sample mode", "Runs on saved data files with no internet or cloud services, so anyone can try it."),
    ("Degraded", "A live source was unavailable and the tool used saved data instead. The brief says so."),
    ("Synthetic data", "Sensor data is NASA's simulated engines; maintenance records, parts and schedules are "
     "generated for this demo."),
]

HOW_TO_READ = [
    "Read the coloured box first: it is the recommendation and its deadline. The chips under it say how the brief "
    "was checked (rule checks, critic rounds, the model that answered).",
    "Read **In plain words** for a one-paragraph explanation.",
    "Check the numbers row: remaining flights by two methods, and which part is wearing.",
    "Look at **Plan** for the task, parts and hangar slot.",
    "If you want proof, open any **EV-** item in Evidence.",
    "Approve to draft a work order, or reject with a comment.",
]


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
            swap = (rec.get("action") or "").lower().startswith("swap")
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
