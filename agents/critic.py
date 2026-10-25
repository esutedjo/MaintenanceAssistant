"""Evidence critic: try to disprove the brief. Deterministic checks always run (sample mode included).
An LLM pass is added when Nightshift is available and SAMPLE_MODE is off. The critic never writes the recommendation."""

import json
import logging
import re
import time

from agents.runtime import NodeResult, RunContext, node
from casefile.schemas import CaseFile, MAX_ROUNDS, Verdict
from models.fault_mode import discriminating
from tools.llm import LlmClient, LlmRefusal, LlmUnavailable

log = logging.getLogger("critic")


def _family(model: str) -> str:
    name = (model or "").lower()
    if "nemotron" in name or name.startswith("nvidia/"):
        return "nvidia"
    if "claude" in name or "anthropic" in name:
        return "anthropic"
    if "gpt" in name or "openai" in name:
        return "openai"
    return name.split("/")[0] or "unknown"


def drivers_vs_fault(case: CaseFile, pack: dict, store=None) -> str | None:
    """SHAP drivers are often common-trend sensors; they must not be presented as the fault-mode signature.
    When a World Model is available the check walks sensor→component and fault→component."""
    if any("distinguish" in c.text.lower() for c in case.findings.claims):
        return None
    modes, drivers = case.findings.fault_modes, case.findings.drivers
    if not modes or not drivers or case.asset is None:
        return None
    variants = pack["variants"].get(case.asset.variant or "", [modes[0].failure_mode])
    if len(variants) < 2:
        return None
    from casefile.world import discriminating_sensors, mode_component, sensor_component
    disc = set(discriminating_sensors(store, pack, modes[0].failure_mode) or
               discriminating(pack, variants).get(modes[0].failure_mode, {}))
    if disc and {d.sensor for d in drivers}.isdisjoint(disc):
        names = ", ".join(pack["sensors"][s]["name"] for s in sorted(disc) if s in pack["sensors"])
        driver_names = ", ".join(pack["sensors"][d.sensor]["name"] for d in drivers[:3] if d.sensor in pack["sensors"])
        gap = (f"RUL drivers listed are {driver_names}, which move under every failure mode. "
               f"The sensors that distinguish {modes[0].failure_mode} are {names}.")
        if store is not None:
            fault_comp = mode_component(store, pack, modes[0].failure_mode, case.asset.variant)
            driver_comps = sorted({sensor_component(store, pack, d.sensor) or d.component for d in drivers} - {None})
            if fault_comp and driver_comps and fault_comp not in driver_comps:
                gap += (f" Those drivers monitor {', '.join(driver_comps)}; "
                        f"the World Model links {modes[0].failure_mode} to {fault_comp}.")
        return gap
    return None


def similar_cases_if_degraded(case: CaseFile) -> str | None:
    b = case.findings.rul_trend
    if b is None:
        return None
    note = (case.evidence.get(b.evidence_id) or type("E", (), {"content": {}})()).content
    if note.get("degradation_detected") and not case.findings.similar_cases:
        return "Degradation is detected but no similar past cases were attached."
    return None


def deterministic_gaps(case: CaseFile, pack: dict, store=None) -> list[str]:
    return [g for g in (drivers_vs_fault(case, pack, store), similar_cases_if_degraded(case)) if g]


def _parse(text: str) -> dict:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        raise ValueError("critic returned no JSON object")
    data = json.loads(match.group())
    outcome = str(data.get("outcome") or "").strip().lower()
    if outcome not in {"accept", "revise", "reject"}:
        raise ValueError(f"invalid critic outcome {outcome!r}")
    raw_gaps = data.get("gaps") or []
    if isinstance(raw_gaps, str):
        raw_gaps = [raw_gaps]
    return {"outcome": outcome, "gaps": [str(g) for g in raw_gaps]}


def _remaining_s(ctx: RunContext) -> float:
    return ctx.settings.run_timeout_s - (time.monotonic() - ctx.started)


def _llm_gaps(case: CaseFile, ctx: RunContext) -> tuple[list[str], str | None, str | None]:
    """Returns (gaps, model_name, error_note). Empty model_name means the LLM was not called."""
    if ctx.settings.sample_mode:
        return [], None, None
    remaining = _remaining_s(ctx)
    if remaining < ctx.settings.critic_timeout_s + 1:
        return [], None, (f"llm_critic_skipped: remaining run time {remaining:.0f}s "
                          f"< critic timeout {ctx.settings.critic_timeout_s:.0f}s")
    evidence_lines = "\n".join(f"- {e.id} [{e.kind}] {e.summary}" for e in case.evidence.values())
    claims = "\n".join(f"- {c.confidence}: {c.text} ({', '.join(c.evidence_ids)})" for c in case.findings.claims)
    rec = case.recommendation
    rec_text = rec.action if rec else "(none)"
    limit = ctx.pack["thresholds"]["rul_divergence_cycles"]
    prompt = (
        "You are an independent evidence critic for a turbofan maintenance brief. Try to DISPROVE it.\n"
        "Reply with one JSON object only, no markdown: "
        "{\"outcome\": \"accept\"|\"revise\"|\"reject\", \"gaps\": [\"...\"]}.\n"
        "Do not invent numbers. Cite evidence IDs already listed.\n"
        f"The two RUL methods are allowed to differ by up to {limit} cycles; that is not a gap.\n"
        "The planning lower bound is min(RUL A 10th percentile, RUL B lower), not an average and not A alone.\n"
        "Trigger severity is computed from the RUL lower bound against the asset-pack thresholds, "
        "not only from the last inspection finding.\n"
        "TF-ENG-9001 is the lease engine in the FMEA; naming it is the lease-engine option.\n"
        "Similar past cases are supporting context, not a requirement that this engine match their removal cycle.\n"
        "Document precedence: airworthiness directive > service bulletin > manual. A mandatory document action "
        "stays in the plan even next to an engine swap; flag it only if it is missing.\n"
        "revise = a specific missing citation or a contradiction with the evidence; "
        "reject = the recommendation is unsafe given the evidence.\n"
        f"Question: {case.question}\nAsset: {case.asset.asset_id if case.asset else None}\n"
        f"Recommendation: {rec_text}\nClaims:\n{claims}\nEvidence:\n{evidence_lines}\n"
    )
    client = LlmClient(ctx.settings)
    reply = None
    try:
        reply = client.chat([{"role": "user", "content": prompt}], model=ctx.settings.meterless_critic_model,
                            temperature=0, max_tokens=1024, app=ctx.settings.meterless_critic_app,
                            response_format={"type": "json_object"}, timeout_s=ctx.settings.critic_timeout_s)
        parsed = _parse(reply.text)
        note = "critic_same_family_as_planner" if _family(reply.model) in {"anthropic", "openai"} else None
        gaps = list(parsed["gaps"])
        if parsed["outcome"] == "reject" and "critic rejected the brief" not in gaps:
            gaps.append("critic rejected the brief")
        return gaps, reply.model, note
    except (LlmUnavailable, LlmRefusal, ValueError, json.JSONDecodeError) as error:
        snippet = (reply.text[:240] if reply and reply.text else "")
        log.warning("llm critic skipped (%s: %s): %s", type(error).__name__, str(error)[:300], snippet)
        return [], (reply.model if reply else None), f"llm_critic_skipped: {type(error).__name__}"
    finally:
        client.close()


@node("critic", "disprove", lambda s: f"round={s.round} claims={len(s.findings.claims)}")
def critic(state: CaseFile, ctx: RunContext) -> NodeResult:
    gaps = deterministic_gaps(state, ctx.pack, ctx.store)
    # Cheap checks first: skip the LLM when we already know we will revise. The LLM may annotate
    # a fully cited brief; it cannot invent a stop-the-line gap (it never produces numbers).
    if gaps:
        llm_gaps, model, note = [], None, None
    else:
        llm_gaps, model, note = _llm_gaps(state, ctx)
    outcome = "accept" if not gaps else "revise"
    if state.round >= MAX_ROUNDS and outcome == "revise":
        outcome = "reject"
    recorded = [*gaps, *[g for g in llm_gaps if g not in gaps]]
    update: dict = {"verdicts": []}
    if note and note.startswith("llm_critic_skipped"):
        update["degraded"] = [f"LLM critic unavailable ({note.split(': ', 1)[-1]}); deterministic critic checks only"]
    if outcome == "revise" and state.round < MAX_ROUNDS:
        update["round"] = state.round + 1
    summary = f"{outcome}; {len(gaps)} gap(s)" + (f"; model={model}" if model else "; deterministic")
    if llm_gaps and outcome == "accept":
        summary += f"; {len(llm_gaps)} LLM note(s) for the engineer"
    if note == "critic_same_family_as_planner":
        summary += "; same family as planner"
    elif note:
        summary += f"; {note}"
    verdict = Verdict(round=state.round, gate="critic", outcome=outcome, gaps=recorded, model=model)
    update["verdicts"] = [verdict]
    retry = max(state.round - 1, 0)
    return NodeResult(update, summary, status="success" if outcome == "accept" else "needs_revision",
                      model=model, retry_count=retry)
