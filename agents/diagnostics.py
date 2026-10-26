"""Diagnostics: fault-mode signature match and similar past cases from case memory (H-MEM). Also runs the tasks the
planner delegates: a check of both candidates when the fault mode is unclear, a check of each inspection finding
against the fault mode (after Knowledge compared it with its limit), and a wider case-memory search when the critic
finds degradation without similar past cases."""

import re

import numpy as np

from agents.runtime import NodeResult, RunContext, node, run_tasks
from casefile.schemas import CaseFile, Claim, EvidenceLedger, Findings, Task
from casefile.shop_findings import findings_claim, shop_finding
from casefile.wear import only_mode
from models.fault_mode import discriminating
from models.features import normalise
from models.rul import relative_to_own_start
from tools.inspections import RESULT_TEXT, mode_for_module
from tools.model_tools import ModelTools

SIMILAR_CASES = 3
COHORT = 500
CANDIDATE_WINDOW = 30           # cycles; the signature matcher reads the last 10
MIN_SHIFT_SD = 0.25             # a sensor that moved less than this does not vote
WARNING_LEAD = re.compile(r"warning_to_removal_cycles=(\d+)")


@node("diagnostics", "match_fault_mode", lambda s: f"asset={s.asset.asset_id if s.asset else None}")
def diagnostics(state: CaseFile, ctx: RunContext) -> NodeResult:
    asset = state.asset
    cycles = ctx.data.cycles(asset.asset_id).frame
    ledger = EvidenceLedger("diagnostics", state.evidence)
    tools = ModelTools(ctx.pack, ledger)
    modes = tools.fault_modes(asset.asset_id, cycles, asset.variant)
    top = modes[0]
    similar = []
    degraded = tools.degradation_detected(cycles)
    if degraded:
        mode = ctx.pack["failure_modes"][top.failure_mode]
        hits = ctx.store.search_memory(f"{mode['label']} {mode['component']} removal", top_n=SIMILAR_CASES,
                                       tags=["case", asset.asset_type, top.failure_mode], threshold=0.0)
        similar = [_case_evidence(ctx, ledger, hit) for hit in hits]
        cohort = ctx.store.search_memory(f"{mode['label']} {mode['component']} removal", top_n=COHORT,
                                         tags=["case", asset.asset_type, top.failure_mode], threshold=0.0)
        leads = []
        for hit in cohort:
            match = WARNING_LEAD.search(hit.content)
            if match and int(match.group(1)) > 0:
                leads.append(int(match.group(1)))
        if leads:
            ordered = sorted(leads)
            median = ordered[len(ordered) // 2]
            ledger.add("case", f"{ctx.store.backend}:hmem/{top.failure_mode}/warning",
                       (f"H-MEM: {len(leads)} past {mode['label']} removals, median {median} cycles "
                        f"from the first moderate finding to removal"),
                       {"failure_mode": top.failure_mode, "n": len(leads), "median_warning_cycles": median},
                       degraded=ctx.store.degraded_reason)
    findings = Findings(fault_modes=modes, similar_cases=similar)
    note = f"; {len(similar)} similar past cases" if similar else "; no degradation, similar-case search skipped"
    head = f"{top.failure_mode} p={top.probability:.2f}"
    if not degraded and only_mode(ctx.pack, asset.variant):       # one-mode fleet: 1.00 is by construction
        head = (f"no wear detected; {top.failure_mode} is this fleet's only known mode "
                f"(p={top.probability:.2f} by construction)")
    return NodeResult({"findings": findings, "evidence": ledger.records}, f"{head}{note}", confidence=top.probability)


def _case_evidence(ctx: RunContext, ledger: EvidenceLedger, hit) -> str:
    """One past removal from case memory as case evidence, with what the shop found on it read out of the record's
    CMMS text (casefile/shop_findings.py); returns its ID."""
    return ledger.add("case", f"{ctx.store.backend}:{hit.source}", hit.content[:240],
                      {"memory_id": hit.id, "content": hit.content, "tags": hit.tags,
                       "trust": hit.confidence, "relevance": round(hit.relevance, 3),
                       "shop_finding": shop_finding(hit.content, ctx.pack)},
                      degraded=ctx.store.degraded_reason).id


def similar_cases_claim(state_evidence: dict, ids: list[str], fallback: str) -> str:
    """The shop findings of the cited past removals in one sentence, or `fallback` when none carries one."""
    items = [f for i in ids if (f := (state_evidence[i].content.get("shop_finding") if i in state_evidence else None))]
    return findings_claim(items) if items and len(items) == len(ids) else fallback


def _search_cases(ctx: RunContext, ledger: EvidenceLedger, asset_type: str, mode: str, tags: list[str]) -> list[str]:
    """Past removals from case memory as case evidence; returns their IDs."""
    spec = ctx.pack["failure_modes"][mode]
    return [_case_evidence(ctx, ledger, hit)
            for hit in ctx.store.search_memory(f"{spec['label']} {spec['component']} removal", top_n=SIMILAR_CASES,
                                               tags=tags, threshold=0.0)]


def check_candidates(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Follow-up for an unclear fault mode: each sensor that tells the top two candidates apart, read over a longer
    recent window (CANDIDATE_WINDOW cycles, the matcher reads 10), votes for the mode whose signature it follows;
    and case memory is searched for the runner-up's past removals too."""
    asset = state.asset
    top, second = state.findings.fault_modes[:2]
    modes = [top.failure_mode, second.failure_mode]
    signatures = discriminating(ctx.pack, modes)
    sensors = sorted({s for sig in signatures.values() for s in sig})
    matcher = ModelTools(ctx.pack, ledger).models.fault_mode
    cycles = ctx.data.cycles(asset.asset_id).frame
    relative = relative_to_own_start(normalise(cycles, sensors, matcher.stats), sensors)
    shift = relative[sensors].tail(CANDIDATE_WINDOW).mean()
    votes = {m: [] for m in modes}
    for sensor in sensors:
        value = float(shift[sensor])
        for mode in modes:
            if signatures[mode][sensor] and np.sign(value) == signatures[mode][sensor] and abs(value) >= MIN_SHIFT_SD:
                votes[mode].append(sensor)
    names = ctx.pack["sensors"]
    counts = {m: len(v) for m, v in votes.items()}
    leader = max(modes, key=lambda m: (counts[m], m == top.failure_mode))
    text = ", ".join(f"{names[s]['name']} {float(shift[s]):+.1f} sd" for s in sensors)
    summary = (f"Discriminating sensors over the last {CANDIDATE_WINDOW} cycles: {text}; "
               + ", ".join(f"{counts[m]} of {len(sensors)} follow {m}" for m in modes)
               + (f"; the longer window favours the runner-up {leader}" if leader != top.failure_mode else ""))
    evidence = ledger.add("model", f"model:fault_mode_window@{ctx.pack['asset_type']}/{ctx.pack['version']}", summary,
                          {"asset_id": asset.asset_id, "window_cycles": CANDIDATE_WINDOW, "candidates": modes,
                           "shift_sd": {s: round(float(shift[s]), 3) for s in sensors},
                           "votes": votes, "vote_counts": counts, "leader": leader,
                           "runner_up_leads": leader != top.failure_mode, "from": top.evidence_id})
    cases = _search_cases(ctx, ledger, asset.asset_type, second.failure_mode,
                          ["case", asset.asset_type, second.failure_mode])
    label = ctx.pack["failure_modes"][second.failure_mode]["label"]
    claims = [Claim(text=summary.replace("Discriminating sensors", "Sensors that tell the two candidates apart") + ".",
                    evidence_ids=[evidence.id], confidence="medium")]
    if cases:
        claims.append(Claim(text=f"{len(cases)} past {label} removals also found in case memory for the runner-up.",
                            evidence_ids=cases, confidence="medium"))
    return (f"{evidence.id}: votes {counts}; {len(cases)} past cases for {second.failure_mode}",
            Findings(similar_cases=cases, claims=claims))


def research_cases(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Repair for "degradation detected but no similar past cases": search again without the fault-mode tag."""
    asset, modes = state.asset, state.findings.fault_modes
    if not modes:
        return "no fault mode to search for", None
    cases = _search_cases(ctx, ledger, asset.asset_type, modes[0].failure_mode, ["case", asset.asset_type])
    if not cases:
        return "case memory returned nothing again; the gap stays", None
    found = similar_cases_claim(ledger.records, cases, f"{len(cases)} similar past removals found in case memory")
    claim = Claim(text=f"{found.rstrip('.')} (wider search, without the fault-mode tag).", evidence_ids=cases,
                  confidence="medium")
    return f"found {len(cases)} past cases ({', '.join(cases)})", Findings(similar_cases=cases, claims=[claim])


def inspection_check(state: CaseFile, ctx: RunContext, task: Task, ledger: EvidenceLedger):
    """Follow-up once Knowledge has compared each finding with its limit: does the finding corroborate the fault mode
    or weaken it? At or beyond its limit on the module a candidate mode wears, it corroborates that mode (or, on the
    runner-up's module, points to the runner-up); within limits on the top mode's module it weakens the reading that
    the wear has gone far; on a module no modelled mode wears (oil system, HPT) it changes neither, and the plan still
    acts on its result. The probabilities are not rewritten: the finding is evidence beside them."""
    pack, asset = ctx.pack, state.asset
    modes = state.findings.fault_modes
    top = modes[0] if modes else None
    lines, claims = [], []
    for a in [i for i in state.findings.inspections if i.current]:
        mode = mode_for_module(pack, a.module, asset.variant)
        result = RESULT_TEXT[a.result]
        module = (pack.get("inspections") or {}).get("modules", {}).get(a.module, a.module)
        if mode is None or top is None:
            effect = "neutral"
            text = (f"Inspection finding {a.evidence_id} on the {module} is {result}; no fault mode of this fleet "
                    "wears that module, so it neither corroborates nor weakens the fault-mode reading, and the plan "
                    "acts on its result.")
        elif a.result in ("at_limit", "beyond"):
            effect = "corroborates" if mode == top.failure_mode else "points_to_runner_up"
            text = (f"Inspection finding {a.evidence_id} on the {module} is {result}, physical wear on the module "
                    f"{pack['failure_modes'][mode]['label']} wears: it "
                    + ("corroborates the primary fault mode." if effect == "corroborates" else
                       f"points to {pack['failure_modes'][mode]['label']} rather than the primary "
                       f"{pack['failure_modes'][top.failure_mode]['label']}."))
        elif mode == top.failure_mode:
            effect = "weakens"
            text = (f"Inspection finding {a.evidence_id} on the {module} is {result}: the wear the sensors attribute "
                    f"to {pack['failure_modes'][mode]['label']} has not reached the manual's limits there, which "
                    "weakens the case for removal on the sensor reading alone.")
        else:
            effect = "neutral"
            text = (f"Inspection finding {a.evidence_id} on the {module} is {result}; it rules out wear beyond limits "
                    f"from {pack['failure_modes'][mode]['label']}, the runner-up, and leaves the primary fault mode "
                    "as it was.")
        evidence = ledger.add("rule", f"inspection:{a.evidence_id}", text, {
            "inspection": a.evidence_id, "result": a.result, "module": a.module, "failure_mode": mode,
            "primary_mode": top.failure_mode if top else None, "effect": effect, "from": a.limit_evidence_id})
        claims.append(Claim(text=text, evidence_ids=[evidence.id, a.evidence_id, *([top.evidence_id] if top else [])],
                            confidence="high" if effect == "corroborates" else "medium"))
        lines.append(f"{evidence.id}: {a.evidence_id} {effect.replace('_', ' ')}")
    return "; ".join(lines) or "no current inspection finding", Findings(claims=claims) if claims else None


@node("diagnostics", "follow_up", lambda s: f"tasks={[t.kind for t in s.pending('diagnostics')]}")
def diagnostics_task(state: CaseFile, ctx: RunContext) -> NodeResult:
    return run_tasks(state, ctx, "diagnostics", {"check_candidates": check_candidates,
                                                 "inspection_check": inspection_check, "repair": research_cases})
