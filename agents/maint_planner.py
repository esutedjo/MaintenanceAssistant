"""Maintenance planner: task, parts, slot and deadline from the RUL lower bound and the FMEA table.
Numbers come from DataTools; this node only selects among them."""

from datetime import date as date_cls, timedelta

from agents.runtime import NodeResult, RunContext, node
from assurance.escalation import parts_wait_days, remaining_shorter_than_wait
from casefile.schemas import CaseFile, Claim, Confidence, EvidenceLedger, PartNeed, Recommendation
from tools.data_tools import DataTools

SEVERITY_RANK = {"none": 0, "minor": 1, "moderate": 2, "severe": 3}


def _severity(lower_cycles: float, pack: dict, inspection: str | None) -> str:
    if lower_cycles < pack["thresholds"]["severe_rul_cycles"]:
        from_rul = "severe"
    elif lower_cycles < pack["thresholds"]["moderate_rul_cycles"]:
        from_rul = "moderate"
    else:
        from_rul = "minor"
    inspect = inspection if inspection in SEVERITY_RANK else "none"
    return from_rul if SEVERITY_RANK[from_rul] >= SEVERITY_RANK[inspect] else inspect


def _inspection_severity(case: CaseFile) -> str | None:
    for evidence in case.evidence.values():
        last = (evidence.content or {}).get("last_inspection")
        if last:
            return last.get("severity")
    return None


def _pick_tasks(tasks: list[dict], severity: str, prefer_inspect: bool) -> list[dict]:
    if prefer_inspect:
        inspect = [t for t in tasks if t["task_type"] == "inspect"]
        if inspect:
            return inspect[:1]
    ranked = [t for t in tasks if t["trigger_severity"] == severity]
    if not ranked:
        ranked = [t for t in tasks if SEVERITY_RANK.get(t["trigger_severity"], 0) <= SEVERITY_RANK[severity]]
    return ranked[:1] or tasks[:1]


@node("maint_planner", "propose_work", lambda s: f"asset={s.asset.asset_id if s.asset else None} round={s.round}")
def maint_planner(state: CaseFile, ctx: RunContext) -> NodeResult:
    pack, findings = ctx.pack, state.findings
    a, b = findings.rul_ml, findings.rul_trend
    if a is None:
        raise ValueError("maintenance planner needs RUL A")
    lower = min(a.lower, b.lower if b else a.lower)
    bound_note = (f"min(A 10th percentile {a.lower:.0f}, B lower {b.lower:.0f})" if b
                  else f"A 10th percentile {a.lower:.0f}")
    cycles_per_day = pack["thresholds"]["cycles_per_day"]
    deadline = ctx.settings.reference_time.date() + timedelta(days=int(lower / cycles_per_day))
    diverge = b is not None and abs(a.rul - b.rul) > pack["thresholds"]["rul_divergence_cycles"]
    modes = findings.fault_modes
    ambiguous = (len(modes) >= 2
                 and abs(modes[0].probability - modes[1].probability) < pack["thresholds"]["ambiguous_fault_probability"])
    top_modes = [m.failure_mode for m in modes[:2]] if ambiguous else ([modes[0].failure_mode] if modes else [])
    severity = _severity(lower, pack, _inspection_severity(state))
    healthy = (a.at_cap or (b is not None and b.at_cap)) and not diverge
    prefer_inspect = diverge or ambiguous or healthy

    ledger = EvidenceLedger("maint_planner", state.evidence)
    tools = DataTools(ctx.data, ledger, pack, ctx.settings.reference_time)
    chosen, part_rows, fmea_ids, catalogue = [], [], [], []
    if not healthy:
        for mode in top_modes or ["hpc_degradation"]:
            fmea = tools.fmea_tasks(mode)
            fmea_ids.append(fmea.id)
            catalogue.extend(fmea.content["tasks"])
            picked = _pick_tasks(fmea.content["tasks"], severity, prefer_inspect)
            chosen.extend(picked)
            for task in picked:
                part_rows.extend(task["parts"])

    slots_ev = next((e for e in state.evidence.values() if e.content.get("first_free") is not None), None)
    if slots_ev is None and not healthy:
        slots_ev = tools.free_slots()
    slot_date = None
    slot_days = 0
    if slots_ev is not None:
        if slots_ev.content.get("first_free"):
            slot_date = date_cls.fromisoformat(slots_ev.content["first_free"])
        slot_days = int(slots_ev.content.get("days_to_first_free") or 0)

    mandates = [d for d in findings.documents if d.mandatory and d.required_task]
    rul_deadline, deadline_doc = deadline, None
    for doc in mandates:
        if doc.within_cycles is not None:
            doc_deadline = ctx.settings.reference_time.date() + timedelta(days=int(doc.within_cycles / cycles_per_day))
            if doc_deadline < deadline:
                deadline, deadline_doc = doc_deadline, doc
        if doc.required_task in {t["fmea_id"] for t in chosen}:
            continue
        task = next((t for t in catalogue if t["fmea_id"] == doc.required_task), None)
        if task is None:
            for mode in doc.failure_modes:
                fmea = tools.fmea_tasks(mode)
                fmea_ids.append(fmea.id)
                catalogue.extend(fmea.content["tasks"])
                task = next((t for t in fmea.content["tasks"] if t["fmea_id"] == doc.required_task), None)
                if task:
                    break
        if task:
            chosen.append(task)
            part_rows.extend(task["parts"])

    swap_task = None
    if not healthy and not prefer_inspect:
        swap_task = next((t for t in catalogue if t["task_type"] == "engine_change"
                          and t["fmea_id"] not in {c["fmea_id"] for c in chosen}), None)
    candidate_rows = [*part_rows, *(swap_task["parts"] if swap_task else [])]
    part_numbers = sorted({p["part_number"] for p in candidate_rows if p.get("part_number")})
    parts_ev = tools.parts(part_numbers) if part_numbers else None
    stock = {p["part_number"]: p for p in (parts_ev.content["parts"] if parts_ev else [])}

    def needs(rows: list[dict]) -> list[PartNeed]:
        out, seen = [], set()
        for row in rows:
            number = row.get("part_number")
            if not number or number in seen:
                continue
            seen.add(number)
            info = stock.get(number, row)
            out.append(PartNeed(part_number=number, qty=max(int(row.get("qty") or 1), 1),
                                available=int(info.get("available") or 0),
                                lead_time_days=int(info.get("lead_time_days") or row.get("lead_time_days") or 0),
                                evidence_ids=[parts_ev.id] if parts_ev else fmea_ids))
        return out

    rul_days = lower / cycles_per_day
    lead_wait = parts_wait_days(needs(part_rows))
    if swap_task and remaining_shorter_than_wait(rul_days, lead_wait, slot_days):
        chosen.insert(0, swap_task)
        part_rows.extend(swap_task["parts"])
    parts = needs(part_rows)

    swap = next((t for t in chosen if t["task_type"] == "engine_change"), None)
    if healthy and mandates:
        action = mandates[0].action
        confidence: Confidence = "high"
    elif healthy:
        action = "Continue in service; next scheduled inspection"
        confidence = "high"
    elif diverge:
        action = "Borescope inspection; do not average the two remaining-life estimates"
        confidence = "low"
    elif ambiguous:
        labels = " and ".join(pack["failure_modes"][m]["label"] for m in top_modes)
        action = f"Inspection targeting both {labels}"
        confidence = "low"
    elif swap:
        action = swap["task"]
        confidence = "high"
    else:
        action = chosen[0]["task"] if chosen else "Review on wing"
        confidence = "medium" if severity != "severe" else "high"
    bad_data = [(e.id, i["detail"]) for e in [*state.evidence.values(), *ledger.records.values()]
                for i in e.content.get("issues") or [] if isinstance(i, dict) and i.get("severity") == "serious"]
    if bad_data:
        action = f"Confirm the input data first; then: {action}"
        confidence = "low"

    short = [p for p in parts if p.available < p.qty]
    planning = ledger.add(
        "rule", f"asset_pack:{pack['asset_type']}/thresholds",
        (f"Trigger severity {severity}: planning lower bound {lower:.0f} cycles "
         f"({bound_note}; severe < {pack['thresholds']['severe_rul_cycles']}, "
         f"moderate < {pack['thresholds']['moderate_rul_cycles']})."),
        {"severity": severity, "rul_lower_cycles": lower, "bound": bound_note,
         "rul_a_lower": a.lower, "rul_b_lower": None if b is None else b.lower,
         "rul_days": round(rul_days, 2),
         "lead_days": lead_wait, "slot_days": slot_days,
         "short_parts": [{"part_number": p.part_number, "available": p.available, "qty": p.qty,
                          "lead_time_days": p.lead_time_days} for p in short]},
    )
    rul_ids = [a.evidence_id, *([b.evidence_id] if b else [])]
    if deadline_doc:
        rationale = [Claim(text=(f"Deadline {deadline} is set by {deadline_doc.doc_id} {deadline_doc.section} "
                                 f"({deadline_doc.within_cycles} cycles), earlier than the RUL lower bound deadline "
                                 f"{rul_deadline}."),
                           evidence_ids=[deadline_doc.evidence_id, *rul_ids], confidence=confidence)]
    else:
        rationale = [Claim(text=f"Deadline {deadline} is the RUL lower bound {lower:.0f} cycles at {cycles_per_day} cycles/day.",
                           evidence_ids=rul_ids, confidence=confidence)]
    if bad_data:
        rationale.insert(0, Claim(
            text=("Input data failed plausibility checks (" + "; ".join(dict.fromkeys(d for _, d in bad_data))[:300]
                  + "); every number below is computed from it, so confidence is low until the data is confirmed."),
            evidence_ids=list(dict.fromkeys(i for i, _ in bad_data)), confidence="low"))
    rationale.append(Claim(text=planning.summary, evidence_ids=[planning.id, *rul_ids], confidence=confidence))
    if parts and parts_ev:
        needed = ", ".join(f"{p.part_number} (need {p.qty}, {p.available} on hand, {p.lead_time_days} d lead)" for p in parts)
        extra = f" Short: {', '.join(p.part_number for p in short)}." if short else ""
        rationale.append(Claim(
            text=f"Selected tasks need {needed}.{extra} In-stock substitutes do not replace a short required part.",
            evidence_ids=[parts_ev.id, planning.id], confidence="high"))
    if swap:
        wait_ids = [*rul_ids, planning.id, *([slots_ev.id] if slots_ev else []), *([parts_ev.id] if parts_ev else [])]
        swap_parts = {p["part_number"] for p in swap["parts"]}
        waiting = [p.part_number for p in short if p.part_number not in swap_parts]
        reason = (f"short parts {', '.join(waiting)} need {lead_wait} d lead time plus next free slot {slot_days} d"
                  if waiting else f"all parts are on hand but the next free slot is {slot_days} d away")
        rationale.append(Claim(
            text=(f"RUL lower bound {rul_days:.1f} days is shorter than the time to repair on wing: {reason}; "
                  f"swap a lease engine and do the shop visit on the removed engine."),
            evidence_ids=wait_ids, confidence="high"))
    for task in chosen:
        rationale.append(Claim(text=f"FMEA task {task['fmea_id']}: {task['task']}.",
                               evidence_ids=fmea_ids or rul_ids, confidence=confidence))
    for task_id, doc in _governing_documents(state, [t["fmea_id"] for t in chosen]).items():
        rationale.append(Claim(
            text=(f"Limits and intervals for {task_id} follow {doc.doc_id} {doc.section} "
                  f"({doc.doc_type.replace('_', ' ')}), the highest-precedence document on that topic."),
            evidence_ids=[doc.evidence_id], confidence="high"))
    for doc in mandates:
        rationale.append(Claim(
            text=(f"{doc.doc_id} {doc.section} mandates {doc.required_task} within {doc.within_cycles} cycles; "
                  "it stays in the plan whatever else is chosen."),
            evidence_ids=[doc.evidence_id, *(fmea_ids or [])], confidence="high"))
    if findings.fault_modes:
        top = findings.fault_modes[0]
        rationale.append(Claim(text=f"Primary fault mode {pack['failure_modes'][top.failure_mode]['label']} "
                                    f"(p={top.probability:.2f}).",
                               evidence_ids=[top.evidence_id], confidence="high" if top.probability >= 0.85 else "medium"))

    last_verdict = state.verdicts[-1] if state.verdicts else None
    extra_claims = []
    if last_verdict and last_verdict.outcome == "revise":
        extra_claims = _revise_claims(state, pack)

    rec = Recommendation(action=action, task_ids=[t["fmea_id"] for t in chosen], parts=parts,
                         slot=slot_date, deadline=deadline, rationale=rationale, confidence=confidence)
    update = {"recommendation": rec, "evidence": ledger.records, "round": max(state.round, 1)}
    if extra_claims:
        from casefile.schemas import Findings
        update["findings"] = Findings(claims=extra_claims)
    return NodeResult(update, f"{action}; deadline {deadline}; {len(parts)} parts", confidence={"low": 0.3, "medium": 0.6, "high": 0.9}[confidence])


def _governing_documents(state: CaseFile, task_ids: list[str]) -> dict:
    """Task → the applicable, non-overridden document section that sets its limits (sections with a topic)."""
    out = {}
    for doc in state.findings.documents:
        content = state.evidence[doc.evidence_id].content
        if doc.overridden_by or not content.get("topic"):
            continue
        for task_id in str(content.get("tasks") or "").split(","):
            if task_id in task_ids and task_id not in out:
                out[task_id] = doc
    return out


def _revise_claims(state: CaseFile, pack: dict) -> list[Claim]:
    """Fill the specific critic gap about SHAP drivers vs discriminating sensors, if that gap is still open."""
    if any("distinguish" in c.text.lower() for c in state.findings.claims):
        return []
    modes = state.findings.fault_modes
    if not modes:
        return []
    top = modes[0]
    from models.fault_mode import discriminating
    sigs = discriminating(pack, pack["variants"].get(state.asset.variant or "", [top.failure_mode]))
    sensors = pack["sensors"]
    names = [sensors[s]["name"] for s in sigs.get(top.failure_mode, {})]
    if not names:
        return []
    return [Claim(text=f"Sensors that distinguish {pack['failure_modes'][top.failure_mode]['label']} "
                       f"from the other candidate are {', '.join(names)} (not the SHAP drivers, which move under both modes).",
                  evidence_ids=[top.evidence_id], confidence="medium")]
