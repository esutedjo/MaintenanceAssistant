"""Evidence critic: try to disprove the brief. Deterministic checks always run (sample mode included).
An LLM pass is added when Nightshift is available and SAMPLE_MODE is off; in sample mode a recorded Nemotron reply is
used only when the prompt matches it exactly (tools/llm_recordings.py), and code checks it exactly as a live one.
Each blocking gap gets a kind and a fixing agent (assurance/routing.py). The critic never writes the recommendation."""

import json
import logging
import re
import time

from agents.runtime import NodeResult, RunContext, node
from assurance.challenges import KINDS, prompt_section, verify
from assurance.notes import fold as fold_notes, screen as screen_notes
from assurance.routing import routes_for
from casefile.decision_log import REFUTED, SCREEN_NOTES
from casefile.schemas import CaseFile, MAX_ROUNDS, Verdict
from models.fault_mode import discriminating
from tools import llm_recordings as recordings
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
    if not isinstance(data, dict):
        raise ValueError("critic reply is not a JSON object")
    outcome = str(data.get("outcome") or "").strip().lower()
    if outcome not in {"accept", "revise", "reject"}:
        raise ValueError(f"invalid critic outcome {outcome!r}")
    # Valid JSON is not a valid reply: coerce every field's type, so model text can never fail the run.
    raw_gaps = data.get("gaps")
    raw_gaps = [raw_gaps] if isinstance(raw_gaps, str) else raw_gaps if isinstance(raw_gaps, list) else []
    raw = data.get("challenges")
    challenges = [c for c in (raw if isinstance(raw, list) else [])
                  if isinstance(c, dict) and isinstance(c.get("kind"), str) and c["kind"] in KINDS]
    return {"outcome": outcome, "gaps": [str(g)[:1000] for g in raw_gaps if isinstance(g, (str, int, float))],
            "challenges": challenges}


def _remaining_s(ctx: RunContext) -> float:
    return ctx.settings.run_timeout_s - (time.monotonic() - ctx.started)


def critic_prompt(case: CaseFile, pack: dict) -> str:
    """What the LLM critic reads. Sample-mode recordings are keyed by the SHA-256 of this exact text."""
    evidence_lines = "\n".join(f"- {e.id} [{e.kind}] {e.summary}" for e in case.evidence.values())
    rec = case.recommendation
    rec_text = rec.action if rec else "(none)"
    limit = pack["thresholds"]["rul_divergence_cycles"]
    return (
        "You are an independent evidence critic for a turbofan maintenance brief. Try to DISPROVE it.\n"
        "Reply with one JSON object only, no markdown: "
        "{\"outcome\": \"accept\"|\"revise\"|\"reject\", \"gaps\": [\"...\"], \"challenges\": [...]}.\n"
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
        f"Recommendation: {rec_text}\n{prompt_section(case)}Evidence:\n{evidence_lines}\n"
    )


def _reply_gaps(text: str, model: str) -> tuple[list[str], list[dict], str | None]:
    """(gaps, challenges, lane note) from a reply, live or recorded. Raises ValueError on a reply that is not JSON."""
    parsed = _parse(text)
    note = "critic_lane_not_nemotron" if _family(model) in {"anthropic", "openai"} else None
    gaps = list(parsed["gaps"])
    if parsed["outcome"] == "reject" and "critic rejected the brief" not in gaps:
        gaps.append("critic rejected the brief")
    return gaps, parsed["challenges"], note


LlmGaps = tuple[list[str], str | None, str | None, list[dict], str | None]


def _recorded_gaps(prompt: str) -> LlmGaps:
    """Sample mode: the recorded Nemotron reply to exactly this prompt, else nothing (deterministic checks only).
    The last item is the recording's label, set whenever a recorded reply was read."""
    recording = recordings.lookup(prompt, "critic")
    if recording is None:
        return [], None, None, [], None
    try:
        gaps, challenges, note = _reply_gaps(recording.text, recording.model)
    except (ValueError, TypeError, KeyError) as error:      # json.JSONDecodeError is a ValueError
        return ([], recording.model, f"llm_critic_skipped: recorded reply unreadable ({type(error).__name__})", [],
                recording.label)
    return gaps, recording.model, note, challenges, recording.label


def _llm_gaps(case: CaseFile, ctx: RunContext) -> LlmGaps:
    """Returns (gaps, model_name, note, challenges, recorded). Empty model_name means no model reply was read;
    `recorded` is the recording's label ("recorded Nemotron response (captured <date>)") for a sample-mode replay."""
    prompt = critic_prompt(case, ctx.pack)
    if ctx.settings.sample_mode:
        return _recorded_gaps(prompt)
    remaining = _remaining_s(ctx)
    if remaining < ctx.settings.critic_timeout_s + 1:
        return [], None, (f"llm_critic_skipped: remaining run time {remaining:.0f}s "
                          f"< critic timeout {ctx.settings.critic_timeout_s:.0f}s"), [], None
    client = LlmClient(ctx.settings)
    reply = None
    try:
        reply = client.chat([{"role": "user", "content": prompt}], model=ctx.settings.meterless_critic_model,
                            temperature=0, max_tokens=1024, app=ctx.settings.meterless_critic_app,
                            response_format={"type": "json_object"}, timeout_s=ctx.settings.critic_timeout_s)
        from tools.status import note_model_reply
        note_model_reply(ctx.settings.meterless_critic_app, reply.model)
        gaps, challenges, note = _reply_gaps(reply.text, reply.model)
        return gaps, reply.model, note, challenges, None
    except (LlmUnavailable, LlmRefusal, ValueError, TypeError, KeyError) as error:
        snippet = (reply.text[:240] if reply and reply.text else "")
        log.warning("llm critic skipped (%s: %s): %s", type(error).__name__, str(error)[:300], snippet)
        return [], (reply.model if reply else None), f"llm_critic_skipped: {type(error).__name__}", [], None
    finally:
        client.close()


def verify_challenges(case: CaseFile, ctx: RunContext, challenges: list[dict], model: str | None
                      ) -> tuple[list[str], list[tuple[dict, str]]]:
    """Code verifies each challenge the model raised: (confirmed gaps that send the brief back, the refuted
    challenges each with its note)."""
    confirmed, refuted = [], []
    reference = ctx.settings.reference_time.date()
    for challenge in challenges:
        try:
            ok, reason = verify(case, ctx.pack, reference, challenge)
        except Exception as error:      # a malformed challenge is refuted, never a failed run
            ok, reason = False, f"the challenge could not be checked ({type(error).__name__})"
        kind = str(challenge.get("kind")).replace("_", " ")
        if ok:
            confirmed.append(f"{kind}: {reason} (raised by {model}, confirmed by code)")
        else:
            refuted.append((challenge, f"{kind} {REFUTED}: {reason}"))
    return confirmed, refuted


def checked_challenges(case: CaseFile, ctx: RunContext, challenges: list[dict], model: str | None
                       ) -> tuple[list[str], list[str]]:
    """Code verifies each challenge the model raised: (confirmed gaps that send the brief back, refuted notes)."""
    confirmed, refuted = verify_challenges(case, ctx, challenges, model)
    return confirmed, [line for _, line in refuted]


def screened_notes(case: CaseFile, notes: list[str], refuted: list[tuple[dict, str]]
                   ) -> tuple[list[str], list[str], str | None]:
    """The model's notes as the engineer sees them: notes code shows false or repeating a refuted challenge dropped,
    near-duplicates folded (assurance/notes.py). Returns (shown, dropped as "<why>: <note>", audit line or None)."""
    kept, dropped = screen_notes(case, notes, [c for c, _ in refuted])
    kept, folded_notes = fold_notes(kept)
    shown_refuted, folded_challenges = fold_notes([line for _, line in refuted], marker=REFUTED)
    folded = folded_notes + folded_challenges
    if not notes and not folded:
        return shown_refuted, dropped, None
    raw = " ".join(f"({n}) {note}" for n, note in enumerate(notes, 1))
    why = "; ".join(f"[{n}] {d.split(': ', 1)[0]}" for n, d in enumerate(dropped, 1))
    audit = (f"{len(notes)} model note(s): {len(kept)} shown, {len(dropped)} dropped"
             + (f" ({why})" if why else "") + f"; {folded} near-duplicate line(s) folded"
             + (f". Raw notes: {raw}" if raw else ""))
    return [*kept, *shown_refuted], dropped, audit


@node("critic", "disprove", lambda s: f"round={s.round} claims={len(s.findings.claims)}")
def critic(state: CaseFile, ctx: RunContext) -> NodeResult:
    gaps = deterministic_gaps(state, ctx.pack, ctx.store)
    # Cheap checks first: skip the LLM when we already know we will revise. On a fully cited brief the LLM's
    # free-text gaps are notes; its checkable challenges send the brief back only when code confirms them.
    if gaps:
        llm_gaps, model, note, challenges, recorded = [], None, None, [], None
    else:
        llm_gaps, model, note, challenges, recorded = _llm_gaps(state, ctx)
    confirmed, refuted = verify_challenges(state, ctx, challenges, model)
    gaps = [*gaps, *confirmed]
    llm_gaps, dropped, audit = screened_notes(state, llm_gaps, refuted)
    outcome = "accept" if not gaps else "revise"
    if state.round >= MAX_ROUNDS and outcome == "revise":
        outcome = "reject"
    listed = [*gaps, *[g for g in llm_gaps if g not in gaps]]
    update: dict = {"verdicts": []}
    if note and note.startswith("llm_critic_skipped"):
        update["degraded"] = [f"LLM critic unavailable ({note.split(': ', 1)[-1]}); deterministic critic checks only"]
    if outcome == "revise" and state.round < MAX_ROUNDS:
        update["round"] = state.round + 1
    # Only the blocking gaps (deterministic checks, challenges code confirmed) get a kind and a fixing agent.
    routes = routes_for(gaps)
    summary = f"{outcome}; {len(gaps)} gap(s)" + (f"; model={model}" if model else "; deterministic")
    if routes:
        summary += " (" + ", ".join(dict.fromkeys(r.kind for r in routes)) + ")"
    if challenges:
        summary += f"; {len(challenges)} challenge(s): {len(confirmed)} confirmed by code, {len(refuted)} refuted"
    if llm_gaps and outcome == "accept":
        summary += f"; {len(llm_gaps)} LLM note(s) for the engineer"
    if dropped:
        summary += f"; {len(dropped)} note(s) dropped by code"
    if note == "critic_lane_not_nemotron":
        summary += "; critic lane not pinned to Nemotron"
    elif note:
        summary += f"; {note}"
    if recorded:
        summary += f"; {recorded}"        # a replay, never a live call: the trace line also says recorded=true
    verdict = Verdict(round=state.round, gate="critic", outcome=outcome, gaps=listed, model=model, routes=routes,
                      recorded=recorded if model else None, dropped_notes=dropped)
    update["verdicts"] = [verdict]
    retry = max(state.round - 1, 0)
    target = {"revise": "planner", "reject": "close"}.get(outcome)
    # The raw notes and why any were dropped, for audit (the decision log skips this line).
    handoffs = [(SCREEN_NOTES, None, audit + (f" ({recorded})" if recorded else ""))] if audit else []
    return NodeResult(update, summary, status="success" if outcome == "accept" else "needs_revision",
                      model=model, recorded=recorded if model else None, retry_count=retry, target_agent=target,
                      handoffs=handoffs)
