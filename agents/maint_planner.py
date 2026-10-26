"""Maintenance planner: task, parts, slot and deadline from the RUL lower bound.
Eligible work comes from the World Model; DataTools only prices parts and slots. An inspection finding is acted on:
beyond limits removes the engine before further flight, at the limit re-inspects within the manual's interval, within
limits answers the inspection the plan would have asked for. On a revise round it rebuilds the plan on what the other
agents added (a mandate Knowledge bound, stock or slots the data agent re-queried, follow-up evidence) and applies the
targeted fix for each gap kind the planner routed to it."""

from datetime import date as date_cls, timedelta

from agents.runtime import NodeResult, RunContext, node
from assurance.escalation import needs_approval, parts_wait_days, pending_adoptions, remaining_shorter_than_wait
from casefile.schemas import (
    RESULT_RANK, CaseFile, Claim, Confidence, EvidenceLedger, Findings, PartNeed, Recommendation,
)
from casefile.wear import no_wear_label, no_wear_text
from casefile.world import asset_state, fmea_extra, mandated_for_mode, tasks_addressing
from tools.data_tools import DataTools
from tools.inspections import RESULT_TEXT, mode_for_module, where

SEVERITY_RANK = {"none": 0, "minor": 1, "moderate": 2, "severe": 3}
# Advice, not an airworthiness determination: the engineer's release process decides.
HEALTHY_ACTION = "No action proposed before the next scheduled inspection"
# The start of the action when an inspection finding is beyond limits; the rule checks look for it.
REMOVE_ACTION = "Remove the engine before further flight"
# The start of the action when the planning bound is at or below 0 cycles or runs out before the first free slot.
NO_DISPATCH = "Do not dispatch"


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
    # Follow-up evidence can only make the plan more cautious, never less.
    candidates = _latest(state, "runner_up_leads")
    if candidates is not None and candidates.content["runner_up_leads"]:
        ambiguous = True                      # the longer sensor window points at the runner-up: inspect both
    top_modes = [m.failure_mode for m in modes[:2]] if ambiguous else ([modes[0].failure_mode] if modes else [])
    severity = _severity(lower, pack, _inspection_severity(state))
    healthy = (a.at_cap or (b is not None and b.at_cap)) and not diverge
    prefer_inspect = diverge or ambiguous or healthy
    fixes = {k for t in state.pending("maint_planner") for k in (t.kind or "").split(",")}
    variant = state.asset.variant if state.asset else None

    # Inspection findings (Knowledge compared each with its limit). The worst current one governs; it can only make
    # the plan more cautious, except that fresh findings within limits on every suspected module answer the
    # inspection a disagreement or an unclear fault would otherwise ask for.
    current = [i for i in findings.inspections if i.current]
    worst = max(current, key=lambda i: RESULT_RANK[i.result]) if current else None
    gate = worst.result if worst else None
    inspected_mode = mode_for_module(pack, worst.module, variant) if worst else None
    suspected = {pack["failure_modes"][m]["component"] for m in top_modes}
    answered = (gate == "within" and (diverge or ambiguous) and bool(suspected)
                and suspected <= {i.module for i in current if i.result == "within" and i.fresh})
    if gate == "beyond":
        severity, healthy, prefer_inspect = "severe", False, False
        top_modes = [inspected_mode] if inspected_mode else top_modes[:1]
    elif gate == "at_limit" and inspected_mode:
        severity = max(severity, "moderate", key=SEVERITY_RANK.__getitem__)
        healthy, prefer_inspect, top_modes = False, False, [inspected_mode]
    # A beyond-limits finding on a module no fault mode wears (oil system, HPT) calls for removal, not module work.
    module_work = not (gate == "beyond" and inspected_mode is None)

    ledger = EvidenceLedger("maint_planner", state.evidence)
    tools = DataTools(ctx.data, ledger, pack, ctx.settings.reference_time)
    chosen, part_rows, world_ids, catalogue = [], [], [], []
    for mode in top_modes or (["hpc_degradation"] if not healthy else []):
        world = _world_tasks(ctx, ledger, mode, variant, tools)
        world_ids.append(world.id)
        catalogue.extend(world.content["tasks"])
        if not healthy and module_work:
            picked = _pick_tasks(world.content["tasks"], severity, prefer_inspect)
            chosen.extend(picked)
            for task in picked:
                part_rows.extend(task["parts"])

    slots_ev = _latest(state, "first_free")          # a re-queried slot list replaces the first one
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
                extra = _world_tasks(ctx, ledger, mode, variant, tools)
                world_ids.append(extra.id)
                catalogue.extend(extra.content["tasks"])
                task = next((t for t in extra.content["tasks"] if t["fmea_id"] == doc.required_task), None)
                if task:
                    break
        if task:
            chosen.append(task)
            part_rows.extend(task["parts"])
    # Mandates Knowledge bound after a gate found one missing, uncited or late: its task, date and citation.
    bindings = [e for e in state.evidence.values() if e.content.get("binding")]
    for binding in bindings:
        comply_by = date_cls.fromisoformat(binding.content["comply_by"])
        doc = next((d for d in mandates if d.evidence_id == binding.content["doc_evidence"]), None)
        if comply_by < deadline:
            deadline, deadline_doc = comply_by, doc or deadline_doc
        world_ids.append(binding.id)
        for task in binding.content["tasks"]:
            if task["fmea_id"] not in {t["fmea_id"] for t in chosen}:
                chosen.append(task)
                part_rows.extend(task["parts"])
    if "task_not_eligible" in fixes:
        listed = {i for e in [*state.evidence.values(), *ledger.records.values()]
                  if ":world/" in (e.source or "") for i in e.content.get("task_ids") or []}
        chosen = [t for t in chosen if t["fmea_id"] in listed]

    swap_task = None
    if not healthy and not prefer_inspect:
        swap_task = next((t for t in catalogue if t["task_type"] == "engine_change"
                          and t["fmea_id"] not in {c["fmea_id"] for c in chosen}), None)
    candidate_rows = [*part_rows, *(swap_task["parts"] if swap_task else [])]
    part_numbers = sorted({p["part_number"] for p in candidate_rows if p.get("part_number")})
    parts_ev = tools.parts(part_numbers) if part_numbers else None
    # Each part cites the record its stock row came from; a data-agent re-query wins over the planner's first read.
    stock = {p["part_number"]: (p, parts_ev.id) for p in (parts_ev.content["parts"] if parts_ev else [])}
    for requery in [e for e in state.evidence.values() if e.content.get("requery") and "parts" in e.content]:
        stock.update({p["part_number"]: (p, requery.id) for p in requery.content["parts"]})

    def needs(rows: list[dict]) -> list[PartNeed]:
        out, seen = [], set()
        for row in rows:
            number = row.get("part_number")
            if not number or number in seen:
                continue
            seen.add(number)
            info, cited = stock.get(number, (row, None))
            out.append(PartNeed(part_number=number, qty=max(int(row.get("qty") or 1), 1),
                                available=int(info.get("available") or 0),
                                lead_time_days=int(info.get("lead_time_days") or row.get("lead_time_days") or 0),
                                evidence_ids=[cited] if cited else [parts_ev.id] if parts_ev else world_ids))
        return out

    rul_days = lower / cycles_per_day
    lead_wait = parts_wait_days(needs(part_rows))
    # Beyond limits grounds the engine now: no remaining life to wait out parts and a slot on wing.
    if swap_task and remaining_shorter_than_wait(0.0 if gate == "beyond" else rul_days, lead_wait, slot_days):
        chosen.insert(0, swap_task)
        part_rows.extend(swap_task["parts"])
    parts = needs(part_rows)

    # Back-test follow-up: when the optimistic method missed wear the last inspection found, inspect at the first
    # free slot instead of waiting for the bound (earlier only; the rule checks allow any deadline before the bound).
    backtest = _latest(state, "optimistic_missed_wear")
    early = None
    if backtest is not None and backtest.content["optimistic_missed_wear"] and slot_date and slot_date < deadline:
        deadline, early = slot_date, backtest
    # The manual's interval for the finding: now for beyond limits, the re-inspection interval at the limit, the
    # routine interval within limits; whichever is earlier, it or the deadline so far, stands.
    reference = ctx.settings.reference_time.date()
    timed = worst if gate in ("beyond", "at_limit") else min(
        (i for i in current if i.result == "within" and i.interval_cycles and i.module in suspected),
        key=lambda i: i.interval_cycles, default=None)
    by_inspection = None
    if timed is not None and (timed.result == "beyond" or timed.interval_cycles):
        due = reference if timed.result == "beyond" else \
            reference + timedelta(days=int(timed.interval_cycles / cycles_per_day))
        if due <= deadline:
            deadline, by_inspection, early = due, timed, None
    if "deadline_after_bound" in fixes:
        deadline = min(deadline, rul_deadline)

    swap = next((t for t in chosen if t["task_type"] == "engine_change"), None)
    if healthy and mandates:
        action = mandates[0].action
        confidence: Confidence = "high"
    elif healthy:
        action = HEALTHY_ACTION
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
    if worst is not None:
        what = f"{where(state.evidence[worst.evidence_id].content['finding'], pack)} {RESULT_TEXT[worst.result]}"
        then = swap or next((t for t in chosen if t["task_type"] != "engine_change"), None)
        if gate == "beyond":
            action = f"{REMOVE_ACTION} ({what})" + (f"; {_lower_first(then['task'])}" if then else "")
            confidence = "high"
        elif gate == "at_limit" and not swap and SEVERITY_RANK[severity] <= SEVERITY_RANK["moderate"]:
            # The module's own work when a fault mode wears it; otherwise the plan the rest of the evidence gives.
            rest = (then["task"] if then and inspected_mode else action if action != HEALTHY_ACTION else "")
            action = f"Re-inspect within {worst.interval_cycles} cycles ({what})" + (f"; {_lower_first(rest)}"
                                                                                     if rest else "")
            confidence = "medium" if inspected_mode else confidence     # on the suspected module it answers
        elif answered:
            kind = (pack.get("inspections") or {}).get("kinds", {}).get(worst.kind, worst.kind).split(" (")[0].lower()
            action = f"No removal ({what}); next {kind} by the deadline"
            confidence = "medium"
    # No flying left before the work can start: a planning bound at or below 0 cycles, or a deadline before the first
    # free slot. Say so first and name the slot (a finding beyond limits already removes the engine before flight).
    grounded = not healthy and gate != "beyond" and (lower <= 0 or (slot_date is not None and deadline < slot_date))
    if grounded:
        at_slot = f" at the first free slot ({slot_date})" if slot_date else ""
        if swap and action == swap["task"]:
            head, _, rest = swap["task"].partition(";")
            action = f"{NO_DISPATCH}; {_lower_first(head.strip())}{at_slot}" + (f"; {rest.strip()}" if rest.strip()
                                                                                 else "")
        else:
            until = f" until the first free slot ({slot_date})" if slot_date else ""
            action = f"{NO_DISPATCH}{until}; {_lower_first(action)}"
    bad_data = [(e.id, i["detail"]) for e in [*state.evidence.values(), *ledger.records.values()]
                for i in e.content.get("issues") or [] if isinstance(i, dict) and i.get("severity") == "serious"]
    reread = _latest(state, "reproduced")
    if bad_data:
        transient = reread is not None and not reread.content["reproduced"]
        action = ("Confirm the input data first (a re-read was clean: re-run the brief on fresh data); then: "
                  if transient else "Confirm the input data first; then: ") + action
        confidence = "low"

    short = [p for p in parts if p.available < p.qty]
    raised = (f" Raised to {severity} by inspection finding {worst.evidence_id} ({RESULT_TEXT[gate]})."
              if gate in ("beyond", "at_limit") and severity != _severity(lower, pack, _inspection_severity(state))
              else "")
    planning = ledger.add(
        "rule", f"asset_pack:{pack['asset_type']}/thresholds",
        (f"Trigger severity {severity}: planning lower bound {lower:.0f} cycles "
         f"({bound_note}; severe < {pack['thresholds']['severe_rul_cycles']}, "
         f"moderate < {pack['thresholds']['moderate_rul_cycles']}).{raised}"),
        {"severity": severity, "rul_lower_cycles": lower, "bound": bound_note,
         "rul_a_lower": a.lower, "rul_b_lower": None if b is None else b.lower,
         "rul_days": round(rul_days, 2),
         "lead_days": lead_wait, "slot_days": slot_days,
         "short_parts": [{"part_number": p.part_number, "available": p.available, "qty": p.qty,
                          "lead_time_days": p.lead_time_days} for p in short],
         **({"inspection": worst.evidence_id, "inspection_result": gate} if worst else {})},
    )
    rul_ids = [a.evidence_id, *([b.evidence_id] if b else [])]
    if by_inspection is not None:
        rationale = [Claim(text=_inspection_deadline_text(by_inspection, deadline, rul_deadline, cycles_per_day),
                           evidence_ids=[by_inspection.evidence_id, by_inspection.limit_evidence_id, *rul_ids],
                           confidence=confidence)]
    elif early is not None:
        rationale = [Claim(text=(f"Deadline {deadline} is the first free slot: the back-test shows the "
                                 f"{early.content['optimistic_method']} estimate missed wear the last inspection "
                                 f"found, so the inspection does not wait for the RUL lower bound deadline "
                                 f"{rul_deadline}."),
                           evidence_ids=[early.id, *([slots_ev.id] if slots_ev else []), *rul_ids],
                           confidence=confidence)]
    elif deadline_doc:
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
    if reread is not None:
        rationale.insert(1 if bad_data else 0, Claim(
            text=("A re-read of the inputs past the run's cache reproduced the plausibility issue: bad source data, "
                  "not a read error." if reread.content["reproduced"] else
                  "A re-read of the inputs past the run's cache did not reproduce the plausibility issue: the first "
                  "read was faulty and this brief is built on it, so re-run the brief before acting on its numbers."),
            evidence_ids=[reread.id], confidence="low" if bad_data else "medium"))
    rationale.append(Claim(text=planning.summary, evidence_ids=[planning.id, *rul_ids, *([worst.evidence_id]
                                                                                       if raised else [])],
                           confidence=confidence))
    if grounded:
        why = ("leaves no flight before the work" if lower <= 0 else
               f"runs out on {deadline}, before the first free slot {slot_date}")
        rationale.append(Claim(
            text=(f"Planning lower bound {lower:.0f} cycles {why}: do not dispatch; the engine stays on the ground "
                  + (f"until the work at the first free slot {slot_date}." if slot_date else "until the work.")),
            evidence_ids=[planning.id, *rul_ids, *([slots_ev.id] if slots_ev else [])], confidence="high"))
    for item in current:
        rationale.append(Claim(text=_inspection_text(item, deadline, answered),
                               evidence_ids=[item.evidence_id, item.limit_evidence_id], confidence="high"))
    if backtest is not None:
        rationale.append(Claim(text=f"{backtest.summary}.", evidence_ids=[backtest.id, backtest.content["from"]],
                               confidence="medium"))
    if candidates is not None and candidates.content["runner_up_leads"]:
        rationale.append(Claim(
            text=(f"Over a {candidates.content['window_cycles']}-cycle window the sensors that tell the candidates "
                  f"apart favour {candidates.content['leader']}, so the plan inspects both modules."),
            evidence_ids=[candidates.id], confidence="low"))
    if parts and parts_ev:
        needed = ", ".join(f"{p.part_number} (need {p.qty}, {p.available} on hand, {p.lead_time_days} d lead)" for p in parts)
        extra = f" Short: {', '.join(p.part_number for p in short)}." if short else ""
        stock_ids = list(dict.fromkeys(i for p in parts for i in p.evidence_ids))
        rationale.append(Claim(
            text=f"Selected tasks need {needed}.{extra} In-stock substitutes do not replace a short required part.",
            evidence_ids=[*(stock_ids or [parts_ev.id]), planning.id], confidence="high"))
    if swap:
        wait_ids = [*rul_ids, planning.id, *([slots_ev.id] if slots_ev else []), *([parts_ev.id] if parts_ev else [])]
        swap_parts = {p["part_number"] for p in swap["parts"]}
        waiting = [p.part_number for p in short if p.part_number not in swap_parts]
        reason = (f"short parts {', '.join(waiting)} need {lead_wait} d lead time plus next free slot {slot_days} d"
                  if waiting else f"all parts are on hand but the next free slot is {slot_days} d away")
        if gate == "beyond":
            rationale.append(Claim(
                text=(f"Inspection finding {worst.evidence_id} grounds the engine now and a repair on wing waits: "
                      f"{reason}; swap a lease engine and do the shop visit on the removed engine."),
                evidence_ids=[worst.evidence_id, *wait_ids], confidence="high"))
        else:
            rationale.append(Claim(
                text=(f"RUL lower bound {rul_days:.1f} days is shorter than the time to repair on wing: {reason}; "
                      f"swap a lease engine and do the shop visit on the removed engine."),
                evidence_ids=wait_ids, confidence="high"))
    for task in chosen:
        rationale.append(Claim(text=f"World Model task {task['fmea_id']}: {task['task']}.",
                               evidence_ids=world_ids or rul_ids, confidence=confidence))
    for task_id, doc in _governing_documents(state, [t["fmea_id"] for t in chosen]).items():
        replaced = next((d for sb, d in pending_adoptions(state) if sb.evidence_id == doc.evidence_id), None)
        if replaced is not None and needs_approval(doc):
            # A recommended SB is not adopted silently: the plan keeps the manual until engineering approves it.
            rationale.append(Claim(
                text=(f"Limits and intervals for {task_id} follow {replaced.doc_id} {replaced.section} "
                      f"({replaced.doc_type.replace('_', ' ')}); {doc.doc_id} {doc.section} "
                      f"({doc.doc_type.replace('_', ' ')}) would replace it on that topic: SB recommended; adopting "
                      "it needs engineering approval."),
                evidence_ids=[replaced.evidence_id, doc.evidence_id], confidence="high"))
            continue
        rationale.append(Claim(
            text=(f"Limits and intervals for {task_id} follow {doc.doc_id} {doc.section} "
                  f"({doc.doc_type.replace('_', ' ')}), the highest-precedence document on that topic."),
            evidence_ids=[doc.evidence_id], confidence="high"))
    for doc in mandates:
        rationale.append(Claim(
            text=(f"{doc.doc_id} {doc.section} mandates {doc.required_task} within {doc.within_cycles} cycles; "
                  "it stays in the plan whatever else is chosen."),
            evidence_ids=[doc.evidence_id, *(world_ids or [])], confidence="high"))
    if findings.fault_modes:
        top = findings.fault_modes[0]
        quiet = no_wear_label(findings.model_dump(), pack, variant)
        if quiet:       # one-mode fleet, healthy engine: p=1.00 is by construction, not a finding
            rationale.append(Claim(text=f"{no_wear_text(quiet)}.", evidence_ids=[top.evidence_id, *rul_ids],
                                   confidence="high"))
        else:
            rationale.append(Claim(text=f"Primary fault mode {pack['failure_modes'][top.failure_mode]['label']} "
                                        f"(p={top.probability:.2f}).",
                                   evidence_ids=[top.evidence_id],
                                   confidence="high" if top.probability >= 0.85 else "medium"))
        if not world_ids:
            world = _world_tasks(ctx, ledger, top.failure_mode, variant, tools)
            world_ids.append(world.id)
        world_ev = next((ledger.records[i] for i in world_ids if i in ledger.records), None)
        listed = (world_ev.content.get("task_ids") if world_ev else []) or []
        used = ", ".join(t["fmea_id"] for t in chosen)
        shown = ", ".join(listed) or "none"
        if healthy or not used:
            text = (f"World Model lists tasks for this fault ({shown}); none selected while the engine "
                    "is still in the healthy band.")
        else:
            text = f"World Model lists tasks for this fault ({shown}); the plan uses {used}."
        rationale.append(Claim(text=text, evidence_ids=world_ids[:1], confidence="high"))
        graph_mandates = mandated_for_mode(ctx.store, pack, top.failure_mode, variant)
        if graph_mandates and world_ev:
            names = ", ".join(f"{m['doc_id']} {m['section']}→{m['task']}" for m in graph_mandates)
            rationale.append(Claim(
                # Graph links, not mandates: which sections apply now and which are mandatory is Knowledge's call.
                text=(f"World Model links these document sections to tasks for this fault: {names}; which apply now "
                      "and which are mandatory is decided by the knowledge agent."),
                evidence_ids=world_ids[:1], confidence="high"))
        prior = asset_state(ctx.store, pack, state.asset.asset_id) if state.asset else {}
        if prior.get("last_decision") and world_ev:
            rationale.append(Claim(
                text=(f"World Model records the last sign-off on this asset as {prior['last_decision']}"
                      + (f" ({prior.get('last_action')})" if prior.get("last_action") else "") + "."),
                evidence_ids=world_ids[:1], confidence="medium"))
    cohort = next((e for e in state.evidence.values() if e.content.get("median_warning_cycles") is not None), None)
    if cohort is not None:
        median = int(cohort.content["median_warning_cycles"])
        age, first, last, age_ids = _cycles_since_moderate(state)
        detail, cited = "", [cohort.id]
        if age is not None:
            detail = f" This engine is {age} cycles past its first moderate finding."
            cited.append(ledger.add(
                "rule", "case_file:history",
                f"First moderate or severe finding at cycle {first}; last observed cycle {last}; {age} cycles since.",
                {"age_cycles": age, "first_moderate_cycle": first, "last_cycle": last, "from": age_ids}).id)
        rationale.append(Claim(
            text=(f"Similar past removals in case memory gave a median of {median} cycles of warning "
                  f"({cohort.content['n']} cases).{detail}"),
            evidence_ids=cited, confidence="medium" if age is None or age < median else "low"))
        if age is not None and age >= median:
            confidence = "low"

    # Targeted fixes for the gaps the planner routed here (assurance/routing.py).
    extra_claims = _revise_claims(state, pack) if "driver_signature" in fixes else []
    if fixes & {"unsupported_number", "uncited_claim"}:
        rationale, withdrawn = _withdraw_unsupported(state, ledger, rationale, pack)
        if withdrawn:
            ledger.add("rule", "case_file:revision",
                       f"Withdrew {len(withdrawn)} claim(s) whose citations or numbers their evidence does not support",
                       {"withdrawn": withdrawn})

    rec = Recommendation(action=action, task_ids=[t["fmea_id"] for t in chosen], parts=parts,
                         slot=slot_date, deadline=deadline, rationale=rationale, confidence=confidence)
    update = {"recommendation": rec, "evidence": ledger.records, "round": max(state.round, 1)}
    done = [t.model_copy(update={"status": "done"}) for t in state.pending("maint_planner")]
    if done:
        update["plan"] = done
    if extra_claims:
        update["findings"] = Findings(claims=extra_claims)
    summary = f"{action}; deadline {deadline}; {len(parts)} parts"
    if fixes - {"rebuild"}:
        summary += f"; fixed for: {', '.join(sorted(fixes - {'rebuild'}))}"
    elif fixes:
        summary += "; rebuilt on the repaired evidence"
    return NodeResult(update, summary, confidence={"low": 0.3, "medium": 0.6, "high": 0.9}[confidence])


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _inspection_deadline_text(item, deadline, rul_deadline, cycles_per_day: float) -> str:
    """Why the deadline is the inspection's: now (beyond limits) or the manual's interval, before the RUL bound."""
    if item.result == "beyond":
        return (f"Deadline {deadline} (the reference date): inspection finding {item.evidence_id} is beyond limits and "
                f"{item.reference or 'the inspector'} requires removal before further flight, before the remaining-life "
                f"deadline {rul_deadline}; the engine does not fly again until it is removed.")
    what = "re-inspection at the limit" if item.result == "at_limit" else "next routine inspection"
    return (f"Deadline {deadline} is the {what} that {item.reference or 'the asset pack'} sets for inspection finding "
            f"{item.evidence_id}: {item.interval_cycles} cycles at {cycles_per_day} cycles/day, earlier than the "
            f"remaining-life deadline {rul_deadline}.")


def _inspection_text(item, deadline, answered: bool) -> str:
    """What the plan does with one current finding, as the manual requires."""
    head = f"Inspection finding {item.evidence_id} is {RESULT_TEXT[item.result]}"
    by = f" per {item.reference}" if item.reference else " (inspector-classified)"
    if item.result == "beyond":
        return f"{head}{by}: the plan removes the engine before further flight; nothing less meets the manual."
    if item.result == "at_limit":
        return (f"{head}{by}: re-inspect within {item.interval_cycles} cycles; the deadline {deadline} is not later "
                "than that interval.")
    if answered:
        return (f"{head}{by}: it answers the inspection the brief would otherwise ask for, so no removal; the next "
                f"inspection is due by {deadline}.")
    return f"{head}{by}: no removal on this finding; the plan follows the remaining-life bound and any mandate."


def _latest(state: CaseFile, key: str):
    """The most recent evidence record that carries `key` (a re-query or follow-up supersedes the first read)."""
    return next((e for e in reversed(list(state.evidence.values())) if e.content.get(key) is not None), None)


def _withdraw_unsupported(state: CaseFile, ledger: EvidenceLedger, rationale: list[Claim], pack: dict
                          ) -> tuple[list[Claim], list[str]]:
    """Drop plan claims that cite missing evidence or state a number their evidence lacks; the deadline claim stays."""
    from assurance.challenges import unsupported_numbers
    probe = state.model_copy(update={"evidence": {**state.evidence, **ledger.records}})
    kept, withdrawn = [], []
    for n, claim in enumerate(rationale):
        bad = probe.unresolved(claim.evidence_ids) or unsupported_numbers(probe, claim, pack)
        if bad and n:
            withdrawn.append(claim.text)
        else:
            kept.append(claim)
    return kept, withdrawn


def _world_tasks(ctx: RunContext, ledger: EvidenceLedger, mode: str, variant: str | None,
                 tools: DataTools | None = None):
    """Eligible tasks from the graph for this fault and variant. Live FMEA extras are recorded as drift."""
    degraded = ctx.store.degraded_reason
    try:
        tasks = tasks_addressing(ctx.store, ctx.pack, mode, variant)
    except Exception as error:
        return ledger.add("rule", f"{ctx.store.backend}:world/{mode}",
                          f"World Model lookup failed ({type(error).__name__})",
                          {"failure_mode": mode, "task_ids": [], "tasks": []},
                          degraded=f"World Model unavailable ({type(error).__name__})")
    ids = [t["fmea_id"] for t in tasks]
    extra = []
    if tools is not None:
        try:
            extra = fmea_extra(ids, [t["fmea_id"] for t in tools.fmea_tasks(mode).content["tasks"]])
        except Exception:
            extra = []
    note = f"; FMEA table lists extra {', '.join(extra)}" if extra else ""
    return ledger.add("rule", f"{ctx.store.backend}:world/{mode}",
                      f"World Model: {len(ids)} tasks address {mode}{note}",
                      {"failure_mode": mode, "task_ids": ids, "tasks": tasks, "fmea_drift_extra": extra},
                      degraded=degraded)


def _cycles_since_moderate(state: CaseFile) -> tuple[int | None, int | None, int | None, list[str]]:
    """Cycles from the first moderate or severe finding to the last observed cycle: (age, first, last, the evidence
    they come from: the asset profile and the maintenance history)."""
    last = first = None
    last_id = first_id = None
    for evidence in state.evidence.values():
        content = evidence.content or {}
        if content.get("fleet_status") and content.get("last_cycle") is not None:
            last, last_id = int(content["last_cycle"]), evidence.id
        for finding in content.get("findings") or []:
            if isinstance(finding, dict) and finding.get("severity") in {"moderate", "severe"} and finding.get("cycle") is not None:
                cycle = int(finding["cycle"])
                if first is None or cycle < first:
                    first, first_id = cycle, evidence.id
    if last is None or first is None:
        return None, None, None, []
    return last - first, first, last, list(dict.fromkeys([last_id, first_id]))


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
