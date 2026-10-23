"""Maintenance planner: task, parts, slot and deadline from the RUL lower bound and the FMEA table.
Numbers come from DataTools; this node only selects among them."""

from datetime import date as date_cls, timedelta

from agents.runtime import NodeResult, RunContext, node
from assurance.escalation import remaining_shorter_than_wait
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
    healthy = a.at_cap or (b is not None and b.at_cap)
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

    rul_days = lower / cycles_per_day
    lead_guess = max((p.get("lead_time_days") or 0 for p in part_rows), default=0)
    if (not healthy and not prefer_inspect
            and remaining_shorter_than_wait(rul_days, lead_guess, slot_days)):
        swap = next((t for t in catalogue if t["task_type"] == "engine_change"), None)
        if swap and swap["fmea_id"] not in {t["fmea_id"] for t in chosen}:
            chosen.insert(0, swap)
            part_rows.extend(swap["parts"])

    part_numbers = sorted({p["part_number"] for p in part_rows if p.get("part_number")})
    parts_ev = tools.parts(part_numbers) if part_numbers else None

    parts = []
    stock = {p["part_number"]: p for p in (parts_ev.content["parts"] if parts_ev else [])}
    seen: set[str] = set()
    for row in part_rows:
        number = row.get("part_number")
        if not number or number in seen:
            continue
        seen.add(number)
        info = stock.get(number, row)
        parts.append(PartNeed(part_number=number, qty=max(int(row.get("qty") or 1), 1),
                              available=int(info.get("available") or 0),
                              lead_time_days=int(info.get("lead_time_days") or row.get("lead_time_days") or 0),
                              evidence_ids=[parts_ev.id] if parts_ev else fmea_ids))

    swap = next((t for t in chosen if t["task_type"] == "engine_change"), None)
    if healthy:
        action = "Continue in service; next scheduled inspection"
        confidence: Confidence = "high"
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

    short = [p for p in parts if p.available < p.qty]
    planning = ledger.add(
        "rule", f"asset_pack:{pack['asset_type']}/thresholds",
        (f"Trigger severity {severity}: planning lower bound {lower:.0f} cycles "
         f"({bound_note}; severe < {pack['thresholds']['severe_rul_cycles']}, "
         f"moderate < {pack['thresholds']['moderate_rul_cycles']})."),
        {"severity": severity, "rul_lower_cycles": lower, "bound": bound_note,
         "rul_a_lower": a.lower, "rul_b_lower": None if b is None else b.lower,
         "rul_days": round(rul_days, 2),
         "lead_days": lead_guess, "slot_days": slot_days,
         "short_parts": [{"part_number": p.part_number, "available": p.available, "qty": p.qty,
                          "lead_time_days": p.lead_time_days} for p in short]},
    )
    rul_ids = [a.evidence_id, *([b.evidence_id] if b else [])]
    rationale = [Claim(text=f"Deadline {deadline} is the RUL lower bound {lower:.0f} cycles at {cycles_per_day} cycles/day.",
                       evidence_ids=rul_ids, confidence=confidence)]
    rationale.append(Claim(text=planning.summary, evidence_ids=[planning.id, *rul_ids], confidence=confidence))
    if parts and parts_ev:
        needed = ", ".join(f"{p.part_number} (need {p.qty}, {p.available} on hand, {p.lead_time_days} d lead)" for p in parts)
        extra = f" Short: {', '.join(p.part_number for p in short)}." if short else ""
        rationale.append(Claim(
            text=f"Selected tasks need {needed}.{extra} In-stock substitutes do not replace a short required part.",
            evidence_ids=[parts_ev.id, planning.id], confidence="high"))
    if swap:
        wait_ids = [*rul_ids, planning.id, *([slots_ev.id] if slots_ev else []), *([parts_ev.id] if parts_ev else [])]
        rationale.append(Claim(
            text=(f"RUL lower bound {rul_days:.1f} days is shorter than parts lead time {lead_guess:.0f} d "
                  f"plus next free slot {slot_days} d; swap a lease engine while the shop visit waits for parts."),
            evidence_ids=wait_ids, confidence="high"))
    for task in chosen:
        rationale.append(Claim(text=f"FMEA task {task['fmea_id']}: {task['task']}.",
                               evidence_ids=fmea_ids or rul_ids, confidence=confidence))
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
