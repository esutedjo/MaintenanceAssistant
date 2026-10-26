"""Data agent: telemetry trend, input plausibility, maintenance history, flight schedule and hangar slots as Evidence,
and each inspection finding an inspector entered for the engine (EV-INS-nn). Also runs the tasks the planner
delegates: follow-ups (inspection history for a disagreement, a re-read when the inputs fail a plausibility check) and
repairs (a targeted re-query of parts or slots a gate found unsupported)."""

import pandas as pd

from agents.runtime import NodeResult, RunContext, node, run_tasks
from assurance.routing import named_parts
from casefile.schemas import INSPECTION_CODE, CaseFile, EvidenceLedger, Task
from tools.data_tools import DataTools
from tools.inspections import load_findings, measured_text, merged, where

# Back-test point when no inspection is on record: this many cycles before the last one (RUL A's features use 30).
LONG_WINDOW = 60


def _profile(state: CaseFile) -> dict:
    """Asset registry row the intake agent already read (last cycle, fleet status)."""
    return next((e.content for e in state.evidence.values()
                 if e.produced_by == "intake" and "last_cycle" in e.content), {})


@node("data", "query_sources", lambda s: f"asset={s.asset.asset_id if s.asset else None}")
def data_agent(state: CaseFile, ctx: RunContext) -> NodeResult:
    asset_id = state.asset.asset_id
    ledger = EvidenceLedger("data", state.evidence)
    tools = DataTools(ctx.data, ledger, ctx.pack, ctx.settings.reference_time)
    telemetry = tools.telemetry(asset_id)
    quality = tools.data_quality(asset_id, telemetry.cycles, _profile(state))
    tools.history(asset_id)
    tools.flight_schedule(asset_id)
    tools.free_slots()
    entered, ignored = inspection_evidence(state, ctx, int(telemetry.cycles.cycle.max()))
    fallback = any(e.degraded for e in ledger.records.values() if e.kind == "query")
    summary = f"{len(ledger.records)} evidence records via {ctx.data.mode}; input data {quality.content['data_quality']}"
    if entered:
        summary += f"; {len(entered)} inspection finding(s) ({', '.join(entered)})"
    update = {"evidence": {**ledger.records, **entered}}
    if ignored:
        update["degraded"] = ignored
    return NodeResult(update, summary, status="fallback" if fallback else "success")


def inspection_evidence(state: CaseFile, ctx: RunContext, last_cycle: int) -> tuple[dict, list[str]]:
    """Each inspection finding for this engine, stored or entered with the question, as EV-INS-nn (oldest first),
    sourced to the inspector who entered it. Fresh: taken 0 to the pack's `fresh_cycles` cycles before the last one.
    A finding after the last telemetry cycle or dated after the reference date is held back: recorded, never assessed
    (so it can never supersede a real finding), and a serious input-plausibility issue (data_quality escalation)."""
    asset_id = state.asset.asset_id
    stored = load_findings(ctx.settings, asset_id)
    given = [f for f in state.inspections if f.asset_id == asset_id]
    ignored = [f"Inspection finding {f.finding_id} names {f.asset_id}, not {asset_id}; ignored"
               for f in state.inspections if f.asset_id != asset_id]
    ledger = EvidenceLedger("data", state.evidence, code=INSPECTION_CODE)
    fresh_cycles = int((ctx.pack.get("inspections") or {}).get("fresh_cycles") or 0)
    reference = ctx.settings.reference_time.date()
    stored_ids = {f.finding_id for f in stored}
    for finding in merged(stored, given):
        checks = []
        if finding.cycle > last_cycle:
            checks.append(f"cycle {finding.cycle} is after the last telemetry cycle {last_cycle}")
        if finding.inspected_on > reference:
            checks.append(f"dated {finding.inspected_on}, after the reference date {reference}")
        held = bool(checks)
        issues = [{"check": "inspection_finding_implausible", "severity": "serious",
                   "detail": f"inspection finding {finding.finding_id} {' and '.join(checks)}; held back, not assessed"}
                  ] if held else []
        record = finding.model_dump(mode="json")
        stated = f"; inspector's result {finding.stated_result.replace('_', ' ')}" if finding.stated_result else ""
        summary = (f"Inspection finding {finding.finding_id} entered by {finding.inspector}"
                   + (" (EXAMPLE DATA)" if finding.example else "") + (" (HELD BACK: implausible)" if held else "")
                   + f": {where(record, ctx.pack)} at cycle {finding.cycle} ({finding.inspected_on}): "
                   f"{measured_text(record)}{stated}; {finding.observation[:160]}")
        ledger.add("inspection", f"entered by {finding.inspector}", summary, {
            "finding": record, "asset_id": asset_id,
            "fresh": not held and 0 <= last_cycle - finding.cycle <= fresh_cycles,
            "cycles_before_last": last_cycle - finding.cycle, "telemetry_last_cycle": last_cycle, "checks": checks,
            "quarantined": held, "issues": issues, "stored": finding.finding_id in stored_ids})
    return ledger.records, ignored


def inspection_history(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Follow-up for a disagreement: every inspection on record, and the point to back-test both methods at (the
    last inspection, or LONG_WINDOW cycles back when there is none)."""
    asset_id = state.asset.asset_id
    history = ctx.data.history(asset_id)
    cycles = ctx.data.cycles(asset_id)
    frame = history.frame
    inspections = frame[frame.wo_type == "inspection"].sort_values("cycle_at")
    first, last = int(cycles.frame.cycle.min()), int(cycles.frame.cycle.max())
    rows = [{"wo_id": r.wo_id, "cycle": int(r.cycle_at), "severity": r.severity or "none", "note": r.note}
            for r in inspections.itertuples() if pd.notna(r.cycle_at) and int(r.cycle_at) < last]
    if rows:
        ref = rows[-1]
        point, label = ref["cycle"], f"last inspection {ref['wo_id']} at cycle {ref['cycle']}, severity {ref['severity']}"
    else:
        ref, point = None, max(first + 20, last - LONG_WINDOW)
        label = f"no inspection on record; cycle {point}, {last - point} cycles back"
    worst = max((r["severity"] for r in rows), key=["none", "minor", "moderate", "severe"].index, default="none")
    summary = (f"Inspection history for the RUL disagreement: {len(rows)} inspection(s), worst finding {worst}; "
               f"back-test point {label}; {last - point} cycles flown since; telemetry cycles {first}-{point} kept "
               f"for the recompute")
    evidence = ledger.add("query", history.source, summary, {
        "asset_id": asset_id, "inspections": rows, "worst_severity": worst, "reference_cycle": point,
        "reference_severity": ref["severity"] if ref else None, "reference_wo": ref["wo_id"] if ref else None,
        "last_cycle": last, "cycles_since": last - point, "window": [first, point]}, degraded=history.degraded)
    return f"{evidence.id}: back-test point cycle {point} ({label}), {last - point} cycles since", None


def reread_input(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Follow-up for implausible inputs: read telemetry and history again past the run's cache and repeat the
    checks. The same issue again is bad source data (confirm with its owner); a clean re-read is a transient read
    error, and the brief, built on the first read, says to re-run."""
    asset_id = state.asset.asset_id
    first = ctx.data.cycles(asset_id).frame
    again = ctx.data.fresh("cycles", asset_id)
    tools = DataTools(_Fresh(ctx), ledger, ctx.pack, ctx.settings.reference_time)
    rechecked = tools.data_quality(asset_id, again.frame, _profile(state))
    before = next(e for e in state.evidence.values() if e.produced_by == "data" and "data_quality" in e.content)
    same_data = first.reset_index(drop=True).equals(again.frame.reset_index(drop=True))
    reproduced = rechecked.content["data_quality"] == before.content["data_quality"]
    rechecked.content.update({"reread_of": before.id, "same_as_first_read": same_data, "reproduced": reproduced})
    verdict = ("the issue reproduces: bad source data, not a read error" if reproduced else
               f"the re-read is {rechecked.content['data_quality']}: the first read was faulty; re-run the brief")
    return f"{rechecked.id}: re-read {len(again.frame)} cycles ({'identical' if same_data else 'different'}); {verdict}", None


def repair(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Targeted re-query for a parts or slot gap, past the run's cache. Says plainly when nothing new came back."""
    gaps = (task.reason or "").split(" | ")
    lines = []
    kinds = set((task.kind or "").split(","))
    if kinds & {"part_not_in_evidence", "stock_mismatch"}:
        rec = state.recommendation
        wanted = named_parts(gaps) or ([p.part_number for p in rec.parts] if rec else [])
        if wanted:
            fresh = DataTools(_Fresh(ctx), ledger, ctx.pack, ctx.settings.reference_time).parts(wanted)
            found = sorted({p["part_number"] for p in fresh.content["parts"]} & set(wanted))
            fresh.content.update({"requery": True, "requested": wanted})
            lines.append(f"{fresh.id}: re-queried {', '.join(wanted)}; " + (
                f"stock rows for {', '.join(found)}" if found else "no stock row came back; the gap stays"))
    if "slot_not_free" in kinds:
        slots = DataTools(_Fresh(ctx), ledger, ctx.pack, ctx.settings.reference_time).free_slots()
        slots.content["requery"] = True
        lines.append(f"{slots.id}: re-queried free slots; first free {slots.content['first_free']}")
    return "; ".join(lines) or "nothing to re-query", None


class _Fresh:
    """DataSource view that skips the run's cache (DataTools calls source.parts / source.free_slots)."""

    def __init__(self, ctx: RunContext):
        self._data = ctx.data

    def __getattr__(self, name: str):
        return lambda *args: self._data.fresh(name, *args)


@node("data", "follow_up", lambda s: f"tasks={[t.kind for t in s.pending('data')]}")
def data_task(state: CaseFile, ctx: RunContext) -> NodeResult:
    return run_tasks(state, ctx, "data", {"inspection_history": inspection_history, "reread_input": reread_input,
                                          "repair": repair})
