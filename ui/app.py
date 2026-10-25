"""Decision brief. Run from the repo root: uv run task ui"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [str(ROOT)] + [p for p in sys.path if Path(p).resolve() != ROOT]

import importlib

import pandas as pd
import streamlit as st

import ui.brief
import ui.fleet
import ui.help
import ui.engine
import ui.methods
import ui.stress

# Streamlit reruns only this script; reload the helpers so edits show up without a server restart.
importlib.reload(ui.brief)
importlib.reload(ui.engine)
importlib.reload(ui.fleet)
importlib.reload(ui.help)
importlib.reload(ui.methods)
importlib.reload(ui.stress)

from casefile.schemas import CaseFile
from data.db import load_pack
from graph.runner import execute_run
from settings import get_settings
from tools.writeback import approve_case, cited_cases, reject_case
from ui.brief import (
    CSS, asset_context, check_summary, decision_boundaries, decision_rows, driving_sensor_figure, escalation_view,
    fault_label, fmt_rul,
    load_trace_file, primary_fault, rul_context, sensor_trend_rows, signature_sensors, similar_case_summaries,
    trace_chips, trace_rows, verdict_headline, verdict_meta, verdict_tone, world_eligibility,
)
from ui.engine import component_probabilities, credit, engine_figure, photos
from ui.fleet import list_active_assets, load_cycles
from ui.help import GLOSSARY, HOW_TO_READ, SECTION_HELP, plain_summary
from ui.methods import drivers_figure, health_index_figure, method_facts
from ui.stress import gate_result, stress_rows
from agents.intake import FLEET_QUESTION
from tools.triage import build_triage, load_triage, save_triage

GOLDEN = [
    ("Healthy · FD001-A001", "What is the status of FD001-A001?"),
    ("HPC swap · FD001-A031", "What should I do about FD001-A031?"),
    ("Fan swap · FD003-A100", "How long can FD003-A100 keep flying and what is wrong with it?"),
    ("Methods disagree · FD001-A070",
     "The two remaining-life estimates for FD001-A070 look different. What should I do?"),
]


def main() -> None:
    st.set_page_config(page_title="Cleris", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    settings = get_settings()
    pack = load_pack()
    _ask_bar(settings)
    brief, fleet, trace_tab, stress_tab, pack_tab, help_tab = st.tabs(
        ["Decision brief", "Fleet", "Trace", "Stress test", "Asset pack", "Help"])
    with brief:
        _brief_page(settings, pack)
    with fleet:
        _fleet_page()
    with trace_tab:
        _trace_page()
    with stress_tab:
        _stress_page(settings, pack)
    with pack_tab:
        _pack_page(pack)
    with help_tab:
        _help_page()


def _explain(key: str) -> None:
    if st.session_state.get("explain", False):
        st.markdown(f'<div class="explain">{SECTION_HELP[key]}</div>', unsafe_allow_html=True)


def _ask_bar(settings) -> None:
    """Question, Run and the golden cases at the top of the page, so a tablet needs no sidebar."""
    if "question" not in st.session_state:
        st.session_state.question = GOLDEN[2][1]
    st.markdown('<div class="brand"><b>Cleris</b><span>the MRO clearance assistant</span></div>',
                unsafe_allow_html=True)
    with st.container(key="ask"):
        ask, run = st.columns([5, 1], vertical_alignment="bottom")
        ask.text_input("Ask about an engine", key="question", placeholder="What should I do about FD001-A031?")
        clicked = run.button("Run", type="primary", width="stretch", key="run_case")
    with st.container(key="grid2-golden"):
        for col, (label, question) in zip(st.columns(len(GOLDEN)), GOLDEN):
            col.button(label, width="stretch", on_click=_set_question, args=(question, True))
    mode = "Sample mode: committed snapshot, no network" if settings.sample_mode else \
        f"Live mode: data {settings.zetaris_mode}"
    result = st.session_state.get("result") or {}
    last = f" · last run {result.get('duration_s', 0):.1f} s" if result else ""
    note, toggle = st.columns([4, 1], vertical_alignment="center")
    note.caption(f"{mode}{last}. Telemetry is NASA C-MAPSS (simulated); maintenance, spares and documents are "
                 "synthetic.")
    toggle.toggle("Explain", value=False, key="explain",
                  help="Show a short plain-language note under each section. The Help tab has a glossary.")
    if clicked or st.session_state.pop("run_now", False):
        _run(settings)


def _set_question(question: str, run: bool = False) -> None:
    st.session_state.question = question
    st.session_state.run_now = run


def _run(settings) -> None:
    question = (st.session_state.question or "").strip()
    if not question:
        st.error("Ask a question first.")
        return
    try:
        with st.spinner("Running specialists and assurance gates…"):
            outcome = execute_run(question, settings)
    except Exception as error:
        st.error(f"{type(error).__name__}: {error}")
        return
    st.session_state.case = outcome.case.model_dump(mode="json")
    st.session_state.result = outcome.result
    st.session_state.decision = None
    st.session_state.cycles = None
    asset = outcome.case.asset
    if asset is not None:
        try:
            frame = load_cycles(asset.asset_id)
            st.session_state.cycles = frame.to_dict(orient="list")
        except Exception:
            st.session_state.cycles = None


def _brief_page(settings, pack: dict) -> None:
    case = st.session_state.get("case")
    if not case:
        st.markdown("#### What is wrong with this engine, how long do I have, and what should I do?")
        st.write("Pick an example engine above, or type a question that names one (e.g. FD001-A031), and press "
                 "**Run**. You get one recommendation to approve or reject, with the evidence behind every number. "
                 "The Help tab explains how to read it.")
        return
    if case.get("degraded"):
        st.warning("Degraded: " + "; ".join(case["degraded"]))
    tone = verdict_tone(case)
    st.markdown(
        f'<div class="verdict {tone}"><h2>{verdict_headline(case)}</h2>'
        f'<div class="meta">{verdict_meta(case)}</div></div>',
        unsafe_allow_html=True,
    )
    chips = check_summary(case)
    if chips:
        st.markdown('<div class="checks">' + "".join(f'<span class="{tone}">{html.escape(text)}</span>'
                                                     for text, tone in chips) + "</div>", unsafe_allow_html=True)
    _explain("verdict")
    ctx = asset_context(case, pack)
    _context_strip(case, ctx)
    for esc in case.get("escalations") or []:
        view = escalation_view(esc)
        extra = "".join(f'<div class="esc-act">{html.escape(note)}</div>' for note in view["notes"])
        if view["proposed"]:
            extra += f'<div class="esc-act">Proposed: {html.escape(" · ".join(view["proposed"]))}</div>'
        st.markdown(f'<div class="esc {view["severity"]}"><div class="esc-title"><span class="esc-sev">'
                    f'{html.escape(view["severity"])}</span>{html.escape(view["title"])}</div>'
                    f'<div class="esc-msg">{html.escape(view["message"])}</div>{extra}</div>',
                    unsafe_allow_html=True)

    _explain("metrics")
    _overview_cards(case, pack)
    _plan_summary(case)
    why = decision_boundaries(case, pack)
    if why:
        with st.container(border=True):
            st.markdown("**Why this plan and not another**")
            st.markdown("\n".join(f"- {line}" for line in why))
            st.caption("Each line is a rule the planner applied, this engine's value, and the value at which the plan "
                       "would change. Computed from the planner's decision record and the asset pack thresholds.")
    summary = plain_summary(case, pack)
    if summary:
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(" ".join(summary), quote=False))
        with st.expander("In plain words", expanded=st.session_state.get("explain", False)):
            st.markdown(f'<div class="plain">{text}</div>', unsafe_allow_html=True)
    _sign_off(settings, case)
    _details(case, ctx, pack)


def _evidence_list(case: dict, ids: list[str], heading: str = "Evidence behind this") -> None:
    evidence = case.get("evidence") or {}
    ids = [i for i in dict.fromkeys(ids) if i in evidence]
    if not ids:
        return
    st.markdown(f"**{heading}**")
    for eid in ids:
        item = evidence[eid]
        degraded = " · degraded" if item.get("degraded") else ""
        with st.expander(f"{eid} · {item.get('summary', '')}{degraded}"):
            st.caption(f"{item.get('kind')} · {item.get('source')} · produced by {item.get('produced_by')}")
            if item.get("degraded"):
                st.warning(item["degraded"])
            st.json(item.get("content") or {})


def _ids_by_agent(case: dict, agent: str) -> list[str]:
    return sorted(k for k, v in (case.get("evidence") or {}).items() if v.get("produced_by") == agent)


def _overview_cards(case: dict, pack: dict) -> None:
    findings = case.get("findings") or {}
    evidence = case.get("evidence") or {}
    fault, prob = primary_fault(findings, pack)
    rc = rul_context(findings, pack)
    a, b = findings.get("rul_ml"), findings.get("rul_trend")
    with st.container(key="grid2-kpis"):
        c1, c2, c3, c4 = st.columns(4)

    with c1.container(border=True):
        st.metric("Flights left · method A (range)", fmt_rul(a, with_range=True),
                  None if "a_lower_days" not in rc else f"≥ {rc['a_lower_days']:.0f} days at 10th pct",
                  delta_color="off", delta_arrow="off")
        with st.popover("Why?", width="stretch"):
            st.write("A machine-learning model trained on 160 engines that ran to failure reads the last 30 "
                     "flights. 8 in 10 similar engines fall inside the range.")
            if a and a.get("note"):
                st.caption(a["note"])
            if a:
                st.caption(f"`{a['evidence_id']}` {evidence.get(a['evidence_id'], {}).get('summary', '')}")

    with c2.container(border=True):
        st.metric("Flights left · method B (trend)", fmt_rul(b),
                  None if "b_days" not in rc else f"≈ {rc['b_days']:.0f} days", delta_color="off", delta_arrow="off")
        with st.popover("Why?", width="stretch"):
            st.write("Combines the sensors that worsen with wear into one health score and projects when it "
                     "reaches the failure level.")
            if b and b.get("note"):
                st.caption(b["note"])
            if "gap" in rc:
                st.caption(f"A and B are {rc['gap']:.0f} flights apart; the limit is {rc['divergence_limit']}.")
            if b:
                st.caption(f"`{b['evidence_id']}` {evidence.get(b['evidence_id'], {}).get('summary', '')}")

    with c3.container(border=True):
        st.metric("Fault mode", fault, None if prob is None else f"p = {prob:.2f}", delta_color="off",
                  delta_arrow="off")
        with st.popover("Why?", width="stretch"):
            st.write("Which part of the engine is wearing, judged from the sensors that move differently for "
                     "each fault.")
            for m in sorted(findings.get("fault_modes") or [], key=lambda m: -m["probability"]):
                st.write(f"{fault_label(m['failure_mode'], pack)}: {m['probability']:.2f}")
            modes = findings.get("fault_modes") or []
            if modes:
                eid = modes[0]["evidence_id"]
                st.caption(f"`{eid}` {evidence.get(eid, {}).get('summary', '')}")

    with c4.container(border=True):
        similar = findings.get("similar_cases") or []
        st.metric("Similar past cases", str(len(similar)))
        with st.popover("Why?", width="stretch"):
            st.write("Past engines removed with the same fault, from case memory.")
            for line in similar_case_summaries(case) or ["None found (engine still in the healthy range)."]:
                st.caption(line)


def _plan_summary(case: dict) -> None:
    rec = case.get("recommendation") or {}
    if not rec:
        intent = case.get("intent") or {}
        if intent.get("needs_clarification"):
            st.info(intent.get("clarification") or "Need a clearer question.")
        if intent.get("clarification") == FLEET_QUESTION:
            triage = load_triage()
            if triage is not None:
                st.markdown("**Engines due within 7 days** (fleet triage; the Fleet tab has the full ranking)")
                _triage_table(triage, 7, "brief_triage")
        return
    esc = html.escape
    facts = []
    if rec.get("deadline"):
        facts.append(f"Deadline <b>{esc(str(rec['deadline']))}</b>")
    if rec.get("slot"):
        facts.append(f"Hangar slot <b>{esc(str(rec['slot']))}</b>")
    parts = rec.get("parts") or []
    if parts:
        short = [p for p in parts if p["available"] < p["qty"]]
        facts.append(f"Parts <b>{len(parts) - len(short)} of {len(parts)} on hand</b>"
                     + (f", longest lead {max(p['lead_time_days'] for p in short)} d" if short else ""))
    if rec.get("task_ids"):
        facts.append("Tasks <b>" + esc(" · ".join(rec["task_ids"])) + "</b>")
    graph = world_eligibility(case)
    line = ""
    if graph:
        used = f"the plan uses {len(graph['used'])}" if graph["used"] else "none selected"
        line = (f"World Model: {len(graph['listed'])} eligible task{'s' if len(graph['listed']) != 1 else ''} "
                f"for this fault ({esc(', '.join(graph['listed']) or 'none')}); {used}. "
                f'<span class="ev-tag">{esc(graph["evidence_id"])}</span>')
        if graph["degraded"]:
            line += f" · {esc(graph['degraded'])}"
    st.markdown(
        f'<div class="plan-card"><div class="plan-label">Plan</div>'
        f'<div class="plan-action">{esc(rec.get("action") or "")}</div>'
        f'<div class="plan-facts">{"".join(f"<span>{f}</span>" for f in facts)}</div>'
        + (f'<div class="plan-graph">{line}</div>' if line else "") + "</div>",
        unsafe_allow_html=True,
    )


def _details(case: dict, ctx: dict, pack: dict) -> None:
    st.divider()
    st.markdown("### Details")
    st.caption("Drill down: each tab shows the working and the evidence records behind one part of the brief.")
    evidence_count = len(case.get("evidence") or {})
    docs = (case.get("findings") or {}).get("documents") or []
    tabs = st.tabs(["Remaining life", "Fault", f"Plan and documents ({len(docs)})", "History",
                    f"Checks and evidence ({evidence_count})"])
    with tabs[0]:
        _tab_life(case, pack)
    with tabs[1]:
        _tab_fault(case, ctx, pack)
    with tabs[2]:
        _tab_plan(case)
        st.markdown("#### Documents")
        _tab_documents(case, pack)
    with tabs[3]:
        _tab_history(case, ctx)
    with tabs[4]:
        _tab_checks(case)
        st.markdown("#### All evidence")
        _evidence(case)


DOC_TYPE = {"airworthiness_directive": "Airworthiness directive", "service_bulletin": "Service bulletin",
            "manual": "Maintenance manual", "procedure": "Internal procedure"}


def _tab_documents(case: dict, pack: dict) -> None:
    docs = (case.get("findings") or {}).get("documents") or []
    evidence = case.get("evidence") or {}
    order = " > ".join(DOC_TYPE.get(t, t) for t in (pack.get("documents") or {}).get("precedence") or [])
    st.caption(f"Sections that apply to this engine and finding, highest precedence first ({order}). "
               "Only revisions in effect on the reference date are used. All documents are SYNTHETIC.")
    if not docs:
        st.info("No document applies to this case.")
        return
    for d in docs:
        ev = evidence.get(d["evidence_id"]) or {}
        content = ev.get("content") or {}
        kind = DOC_TYPE.get(d["doc_type"], d["doc_type"])
        with st.container(border=True):
            badge = ("**MANDATORY** · " if d.get("mandatory") else
                     "overridden · " if d.get("overridden_by") else "")
            st.markdown(f"{badge}**{d['doc_id']} {d['section']}: {d['heading']}**  \n"
                        f"{kind} · revision {d['revision']} · effective {d['effective_date']} · "
                        f"`{d['evidence_id']}`")
            if d.get("mandatory"):
                st.error(f"Required: {d.get('action')} (FMEA task {d.get('required_task')}, within "
                         f"{d.get('within_cycles')} cycles). Applies because {content.get('mandate_reason')}.")
            elif d.get("overridden_by"):
                st.warning(f"On this topic, {d['overridden_by']} takes precedence (higher document type).")
            elif content.get("mandate"):
                st.caption(ev.get("summary", "").split("]: ", 1)[-1])
            st.write(content.get("text", ""))
            st.caption(f"Relevance to this finding {d['relevance']:.2f} · source {ev.get('source', '')}")
    retrieval = next((e for e in evidence.values()
                      if e.get("produced_by") == "knowledge" and e.get("kind") == "query"), None)
    if retrieval and retrieval["content"].get("excluded"):
        st.markdown("**Not used**")
        for item in retrieval["content"]["excluded"]:
            st.caption(f"{item['chunk']}: {item['reason']}")


def _tab_life(case: dict, pack: dict) -> None:
    findings = case.get("findings") or {}
    rc = rul_context(findings, pack)
    a, b, anomaly = findings.get("rul_ml"), findings.get("rul_trend"), findings.get("anomaly")
    with st.container(key="grid2-life"):
        d1, d2, d3, d4 = st.columns(4)
    d1.metric("Method A", fmt_rul(a, with_range=True), help="LightGBM, 10th–90th percentile range.")
    d2.metric("Method B", fmt_rul(b), help="Health-index trend extrapolated to the failure threshold.")
    if "gap" in rc:
        d3.metric("A vs B gap", f"{rc['gap']:.0f} cycles",
                  "diverged → inspect" if rc["diverged"] else f"within {rc['divergence_limit']}",
                  delta_color="inverse" if rc["diverged"] else "off",
                  help="Above the limit, confidence drops and a borescope inspection is recommended. "
                       "A method at the cap still counts: one method seeing wear and the other not is a disagreement.")
    else:
        d3.metric("A vs B gap", "—")
    if anomaly:
        d4.metric("Health index", f"{anomaly['health_index']:.2f}",
                  f"{anomaly['residual_sigma']:+.1f} sd vs fleet", delta_color="inverse" if anomaly["flagged"] else "off",
                  help=f"Fleet median at cycle {anomaly['cycle']} is {anomaly['fleet_median']:.2f}; "
                       f"flag at +{pack['thresholds']['anomaly_sigma']} sd.")
    facts = method_facts(pack)
    fa, fb = facts["a"], facts["b"]
    st.markdown("**Why two methods?**")
    st.write("No single model is right for every engine. The tool asks two methods that work in completely "
             "different ways. When they agree, you can trust the number more. When they disagree by more than "
             f"{rc['divergence_limit']} flights, the tool treats that as a warning, lowers its confidence and "
             "suggests a borescope inspection. It never averages them.")

    with st.container(key="stack-methods"):
        left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("#### Method A · learned from past engines")
        st.caption("Machine learning (LightGBM gradient-boosted trees)")
        st.markdown(
            f"**In one sentence:** it compares this engine's recent sensor readings with "
            f"{facts['train_engines']} past engines that ran until they failed, and asks how many flights "
            f"engines that looked like this had left.\n\n"
            "**How it works**\n"
            "1. Clean the readings: correct for operating condition (altitude, speed, throttle).\n"
            f"2. For each of {len(fa['sensors'])} sensors, compute the latest value plus its average and trend "
            f"over the last 10 and {fa['window']} flights ({fa['features']} inputs in total).\n"
            f"3. Hundreds of small decision trees each nudge the estimate; their sum is the answer, capped at "
            f"{facts['cap']:.0f} flights.\n"
            "4. Two extra models estimate the pessimistic (10th percentile) and optimistic (90th) cases.\n\n"
            f"**Reads:** {', '.join(fa['sensors'])}.\n\n"
            f"**Track record** on {fa['engines']} engines not used for training: typically off by "
            f"**{fa['mae']} flights** (average error); its range contained the true answer "
            f"**{fa['interval_coverage']:.0%}** of the time (aim: 80%).\n\n"
            "**Good at:** any stage of life; gives a range. **Blind spot:** it can only recognise patterns it "
            "has seen before, and it does not say which part is worn.")
        st.markdown(f"**This engine:** {fmt_rul(a, with_range=True)} flights"
                    + (f" (≈ {rc['a_days']:.0f} days; at least {rc['a_lower_days']:.0f} at the 10th percentile)"
                       if "a_days" in rc else ""))
        fig = drivers_figure(findings.get("drivers") or [], pack)
        if fig:
            st.caption("What pushed this engine's estimate down (SHAP: flights added or removed by each sensor)")
            st.plotly_chart(fig, width="stretch", key="drivers_chart")

    with right.container(border=True):
        st.markdown("#### Method B · follow the wear trend")
        st.caption("Health index plus exponential extrapolation")
        th = fb["thresholds"]
        st.markdown(
            "**In one sentence:** it rolls the sensors that worsen with wear into one health score, draws the "
            "curve the score is following, and finds when that curve reaches the level at which past engines "
            "failed.\n\n"
            "**How it works**\n"
            f"1. Measure each sensor against this engine's own first {fb['baseline_cycles']} flights (engines "
            "start with different amounts of wear).\n"
            f"2. Combine {len(fb['sensors'])} sensors into one health index: about 0 when new, about 1 at failure.\n"
            f"3. If the index has left the healthy band (above {fb['healthy_band']}), fit an accelerating "
            "(exponential) curve through it.\n"
            f"4. Extend the curve until it reaches the failure level seen in past engines: {th['p10']}–{th['p90']} "
            f"(middle {th['p50']}). The flights until then are the estimate.\n\n"
            f"**Reads:** {', '.join(fb['sensors'])}. These move the same way whatever part is wearing.\n\n"
            f"**Track record** on {fb['engines']} engines: typically off by **{fb['mae']} flights** overall; it is "
            f"most accurate close to the end of life. It gave no estimate for {fb['not_fitted']} engines that were "
            "still healthy (it then shows the cap).\n\n"
            "**Good at:** simple, transparent, strong near failure. **Blind spot:** needs at least "
            f"{fb['min_history']} flights of history and a visible trend; its range reflects only the spread of "
            "failure levels, so it looks narrower than it really is.")
        st.markdown(f"**This engine:** {fmt_rul(b, with_range=bool(b) and not b.get('at_cap'))} flights"
                    + (f" (≈ {rc['b_days']:.0f} days)" if "b_days" in rc else "")
                    + (f". {b['note']}" if b and b.get("note") else ""))
        raw = st.session_state.get("cycles")
        if raw:
            fig, note = health_index_figure(pd.DataFrame(raw), pack)
            if fig:
                st.caption("This engine's health index, the fitted trend and the failure level")
                st.plotly_chart(fig, width="stretch", key="health_chart")
            if note:
                st.caption(note)

    st.markdown("**How the two are used together**")
    st.dataframe(pd.DataFrame([
        {"": "Approach", "Method A": "Pattern match against past engines", "Method B": "Extend this engine's own trend"},
        {"": "Looks at", "Method A": f"Last {fa['window']} flights", "Method B": "Whole life of the engine"},
        {"": "Typical error", "Method A": f"{fa['mae']} flights", "Method B": f"{fb['mae']} flights"},
        {"": "Strongest", "Method A": "Early and mid life", "Method B": "Close to failure"},
        {"": "Range shown", "Method A": "10th–90th percentile", "Method B": "Spread of failure levels only"},
    ]), hide_index=True, width="stretch")
    st.write(f"The deadline uses the more cautious of A's 10th percentile and B's lower bound, at "
             f"{rc['cycles_per_day']} flights a day. On the active fleet, A and B disagree by more than "
             f"{rc['divergence_limit']} flights for {facts['divergence_share']:.0%} of engines; those engines get an "
             "inspection recommendation.")
    _evidence_list(case, [x["evidence_id"] for x in (a, b, anomaly) if x])


def _tab_fault(case: dict, ctx: dict, pack: dict) -> None:
    findings = case.get("findings") or {}
    profile = ctx.get("profile") or {}
    if profile.get("possible_modes"):
        st.caption(f"Variant {profile['dataset']} can fail by: {', '.join(profile['possible_modes'])}.")
    modes = sorted(findings.get("fault_modes") or [], key=lambda m: -m["probability"])
    _engine_view(case, ctx, pack)
    if modes:
        st.dataframe(pd.DataFrame([{"fault mode": fault_label(m["failure_mode"], pack),
                                    "probability": round(m["probability"], 2)} for m in modes]),
                     hide_index=True, width="stretch")
    _chart(case, pack)
    telemetry = ctx.get("telemetry")
    if telemetry:
        st.markdown("**Biggest sensor changes**")
        st.caption(f"Mean of the last 10 cycles vs the first {telemetry['baseline_cycles']}, in standard deviations.")
        st.dataframe(pd.DataFrame(sensor_trend_rows(telemetry)), hide_index=True, width="stretch")
    ids = [m["evidence_id"] for m in modes[:1]]
    ids += [d["evidence_id"] for d in (findings.get("drivers") or [])[:1]]
    ids += [telemetry["id"]] if telemetry else []
    _evidence_list(case, ids)


def _engine_view(case: dict, ctx: dict, pack: dict) -> None:
    findings = case.get("findings") or {}
    probs = component_probabilities(findings, pack)
    worn = not rul_context(findings, pack)["healthy"]
    drivers = [d["sensor"] for d in findings.get("drivers") or []]
    signature = signature_sensors(pack, (ctx.get("profile") or {}).get("dataset"))
    st.markdown("**Where on the engine**")
    fig = engine_figure(pack, probs, drivers, signature, worn=worn)
    if fig:
        st.plotly_chart(fig, width="stretch", key="engine_diagram")
    shade = ("Red shading: the module the tool suspects, darker = more likely." if worn else
             "Amber outline: the only module this engine type is known to wear, but there is no clear sign of "
             "wear yet.")
    st.caption(f"{shade} Sensor labels sit above the module they measure: amber = pushed the remaining-life "
               "estimate down (SHAP driver), cyan = tells the fault modes apart, grey = monitored. Not to scale.")
    if not probs:
        return
    component = max(probs, key=probs.get)
    pics = photos(pack, component)
    if not pics:
        return
    name = (pack.get("components") or {}).get(component, component)
    st.markdown(f"**What the {name.lower()} looks like**")
    st.caption((pack.get("images") or {}).get("disclaimer", ""))
    for col, item in zip(st.columns(len(pics)), pics):
        col.image(item["path"], width="stretch")
        col.caption(credit(item))


def _tab_plan(case: dict) -> None:
    _plan(case.get("recommendation") or {})
    _evidence_list(case, _ids_by_agent(case, "maint_planner"))


def _tab_history(case: dict, ctx: dict) -> None:
    _explain("context")
    history, schedule, slots = ctx.get("history"), ctx.get("schedule"), ctx.get("slots")
    if history:
        by_type = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in (history.get("by_type") or {}).items())
        st.write(f"{history['work_orders']} work orders ({by_type or 'none'})")
        last = history.get("last_inspection")
        if last:
            st.markdown(f"Last inspection **{last['wo_id']}** at cycle {last['cycle']} "
                        f"({(last.get('date') or '')[:10]}), severity **{last['severity']}**: {last['note']}")
        if history.get("findings"):
            st.dataframe(pd.DataFrame(history["findings"]), hide_index=True, width="stretch")
        st.caption("Synthetic CMMS records generated from the degradation curves.")
    if schedule:
        st.write(f"Flying {schedule['cycles_next_7_days']} cycles in the next 7 days and "
                 f"{schedule['cycles_next_30_days']} in 30 days; scheduled through "
                 f"{schedule.get('last_scheduled') or '—'}.")
    if slots:
        st.write(f"{slots['free_slots']} free hangar slots; first on {slots['first_free']} "
                 f"({slots['days_to_first_free']} days out).")
    similar = similar_case_summaries(case)
    if similar:
        st.markdown("**Similar past cases**")
        for line in similar:
            st.caption(line)
    ids = [x["id"] for x in (history, schedule, slots) if x]
    _evidence_list(case, ids + ((case.get("findings") or {}).get("similar_cases") or []))


def _tab_checks(case: dict) -> None:
    _decision_path(case)
    for esc in case.get("escalations") or []:
        st.markdown(f"**{esc.get('severity', '').upper()} · {esc.get('rule', '').replace('_', ' ')}**: "
                    f"{esc.get('message', '')} " + " ".join(f"`{i}`" for i in dict.fromkeys(esc.get("evidence_ids") or [])))
    chips = trace_chips(case.get("trace") or [])
    if chips:
        st.caption("Trace · " + " → ".join(chips))
        st.caption("Full step-by-step log in the Trace tab at the top.")


def _context_strip(case: dict, ctx: dict) -> None:
    profile, schedule, slots = ctx.get("profile"), ctx.get("schedule"), ctx.get("slots")
    result = st.session_state.get("result") or {}
    mode = result.get("mode") or {}
    items = []
    if profile:
        items.append(f"<b>{profile['asset_id']}</b> {profile['esn']} · {profile['dataset']} · {profile['status']}")
        items.append(f"cycle <b>{profile['last_cycle']}</b> on {profile['last_seen']}")
    if schedule:
        items.append(f"<b>{schedule['cycles_next_7_days']}</b> cycles planned in 7 d, "
                     f"<b>{schedule['cycles_next_30_days']}</b> in 30 d")
    if slots and slots.get("first_free"):
        items.append(f"first free slot <b>{slots['first_free']}</b>")
    if mode:
        items.append(f"data {mode.get('data')} · memory {mode.get('context_store')}")
    if items:
        st.markdown('<div class="context">' + "".join(f"<span>{i}</span>" for i in items) + "</div>",
                    unsafe_allow_html=True)


def _chart(case: dict, pack: dict) -> None:
    raw = st.session_state.get("cycles")
    findings = case.get("findings") or {}
    if not raw:
        return
    variant = (case.get("asset") or {}).get("variant")
    signature = signature_sensors(pack, variant)
    options = ["SHAP drivers (RUL A)"] + (["Fault-mode signature"] if signature else [])
    st.markdown("**Sensor trends**")
    _explain("chart")
    view = st.radio("Show", options, horizontal=True, label_visibility="collapsed", key="chart_view")
    if view.startswith("SHAP"):
        sensors = findings.get("drivers") or []
        note = ("Sensors that pull RUL A down most. In FD003 these move under both fault modes, so they say "
                "how worn the engine is, not which module.")
    else:
        sensors = [{"sensor": s} for s in signature]
        note = "Sensors whose direction differs between fan and HPC degradation; the fault-mode matcher reads these."
    fig = driving_sensor_figure(pd.DataFrame(raw), sensors, pack)
    if fig is None:
        st.caption("No sensor series for this view.")
        return
    st.plotly_chart(fig, width="stretch")
    st.caption(note)


def _decision_path(case: dict) -> None:
    rows = decision_rows(case)
    if not rows:
        return
    rounds = max(r["round"] for r in rows)
    critic = [r for r in rows if r["gate"] == "critic"]
    revisions = sum(r["outcome"] == "revise" for r in critic)
    _explain("decision")
    st.write(f"{rounds} round(s); the critic asked for {revisions} revision(s) before "
             f"{'accepting' if critic and critic[-1]['outcome'] == 'accept' else 'closing'} the brief. "
             f"{len(case.get('escalations') or [])} escalation rule(s) fired.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Rule checks and escalation rules are deterministic. The critic runs deterministic checks first; "
               "an LLM pass is added only outside sample mode.")


def _plan(rec: dict) -> None:
    if not rec:
        st.caption("No plan for this case.")
        return
    _explain("plan")
    st.markdown(f"**{rec.get('action') or ''}**")
    st.markdown("**Why**")
    for claim in rec.get("rationale") or []:
        tags = " ".join(f"`{i}`" for i in claim.get("evidence_ids") or [])
        st.markdown(f"- {claim.get('text', '')} {tags}")
    parts = rec.get("parts") or []
    if parts:
        st.dataframe(
            pd.DataFrame([{
                "part": p["part_number"], "qty": p["qty"], "on hand": p["available"],
                "lead time (days)": p["lead_time_days"],
            } for p in parts]),
            hide_index=True, width="stretch",
        )
    meta = []
    if rec.get("slot"):
        meta.append(f"Hangar slot **{rec['slot']}**")
    if rec.get("deadline"):
        meta.append(f"Deadline **{rec['deadline']}**")
    if rec.get("task_ids"):
        meta.append("Tasks " + ", ".join(f"`{t}`" for t in rec["task_ids"]))
    if meta:
        st.markdown(" · ".join(meta))


def _evidence(case: dict) -> None:
    evidence = case.get("evidence") or {}
    _explain("evidence")
    for eid, item in sorted(evidence.items()):
        degraded = " · degraded" if item.get("degraded") else ""
        with st.expander(f"{eid} · {item.get('kind')} · {item.get('summary', '')}{degraded}"):
            st.caption(f"{item.get('source')} · {item.get('produced_by')}")
            if item.get("degraded"):
                st.warning(item["degraded"])
            st.json(item.get("content") or {})


def _sign_off(settings, case: dict) -> None:
    decision = st.session_state.get("decision")
    if decision:
        rec = decision["record"]
        if decision["kind"] == "approved":
            where = "CMMS" if rec.get("backend") == "cmms" else rec.get("path", "local file")
            st.success(f"Draft work order **{rec['wo_id']}** written to {where}.")
        else:
            st.info(f"Rejected {rec['wo_id']}. Comment stored with the case.")
        if rec.get("cmms_error"):
            st.warning(f"CMMS insert failed ({rec['cmms_error']}); saved locally instead.")
        applied = (rec.get("memory") or {}).get("case_feedback") or []
        if applied:
            st.caption("Case memory updated: " + "; ".join(
                f"{f['evidence_id']} {FEEDBACK_LABELS[f['kind']].lower()}"
                + (f" (trust {f['trust']:.2f} to {f['trust_after']:.2f})" if f.get("trust_after") is not None
                   and f.get("trust") is not None else "")
                for f in applied))
        return
    if case.get("status") != "needs_review":
        if case.get("status") == "insufficient_evidence":
            gaps = [g for v in case.get("verdicts") or [] for g in v.get("gaps") or []]
            if gaps:
                st.error("Gaps: " + "; ".join(dict.fromkeys(gaps)))
        return
    st.subheader("Your decision")
    _explain("signoff")
    comment = st.text_area("Comment (optional)", key="signoff_comment", height=70)
    cited = cited_cases(CaseFile.model_validate(case))
    feedback = {}
    if cited:
        with st.expander(f"Rate the {len(cited)} cited past case{'s' if len(cited) != 1 else ''} (optional)"):
            feedback = _case_feedback_form(CaseFile.model_validate(case))
    left, right = st.columns(2)
    if left.button("Approve and draft work order", type="primary", key="approve_case", width="stretch"):
        record = approve_case(CaseFile.model_validate(case), settings, comment, case_feedback=feedback)
        st.session_state.decision = {"kind": "approved", "record": record}
        st.session_state.case = {**case, "status": "approved"}
        st.rerun()
    if right.button("Reject", key="reject_case", width="stretch"):
        record = reject_case(CaseFile.model_validate(case), settings, comment, case_feedback=feedback)
        st.session_state.decision = {"kind": "rejected", "record": record}
        st.session_state.case = {**case, "status": "rejected"}
        st.rerun()


FEEDBACK_LABELS = {"helpful": "Helpful", "not_helpful": "Not helpful", "wrong": "Wrong"}


def _case_feedback_form(case: CaseFile) -> dict[str, str]:
    """Per cited past case: follow the decision (approve = helpful, reject = not helpful) or mark it."""
    cited = cited_cases(case)
    if not cited:
        return {}
    st.caption("Similar past cases cited in this brief. Your verdict moves their trust in case memory.")
    out = {}
    for item in cited:
        choice = st.radio(f"{item['evidence_id']} · {item['summary'][:110]}",
                          ["Follow my decision", *FEEDBACK_LABELS.values()], horizontal=True,
                          key=f"fb_{item['memory_id']}")
        if choice != "Follow my decision":
            out[item["memory_id"]] = next(k for k, v in FEEDBACK_LABELS.items() if v == choice)
    return out


TRIAGE_COLUMNS = {"asset_id": "engine", "decision": "decision", "deadline": "deadline", "days_to_deadline": "days left",
                  "lower_bound_cycles": "bound (cycles)", "fault_mode": "fault", "escalations": "escalations"}


def _triage_table(triage: pd.DataFrame, due_days: int | None, key: str) -> pd.DataFrame:
    shown = triage if due_days is None else triage[triage["days_to_deadline"] <= due_days]
    pack = load_pack()
    table = shown.assign(fault_mode=shown["fault_mode"].map(lambda m: fault_label(m, pack) if m else ""))
    st.dataframe(table[list(TRIAGE_COLUMNS)].rename(columns=TRIAGE_COLUMNS), hide_index=True, width="stretch", key=key,
                 height=min(38 + 35 * len(shown), 420))
    return shown


def _fleet_page() -> None:
    triage = load_triage()
    if triage is None:
        st.info("No fleet triage yet. Run `uv run task fleet-triage`, or press Recompute below.")
        assets = list_active_assets()
    else:
        counts = triage["decision"].value_counts()
        st.markdown('<div class="checks">' + "".join(
            f'<span class="{"flag" if d != "keep flying" else "ok"}">{int(counts.get(d, 0))} {d}</span>'
            for d in ("remove, swap or repair", "inspect", "keep flying")) + "</div>", unsafe_allow_html=True)
        due = st.radio("Due within", ["7 days", "30 days", "All engines"], horizontal=True, key="triage_due")
        shown = _triage_table(triage, {"7 days": 7, "30 days": 30}.get(due), "triage_table")
        st.caption(f"Ranked by decision, then deadline. Computed {triage.attrs.get('generated_at') or ''} by running "
                   f"the full agent graph on all {len(triage)} active engines in sample mode (`uv run task "
                   "fleet-triage`). Live data can differ: open an engine for its live brief.")
        assets = shown
    pick = st.selectbox("Open an engine", assets["asset_id"].tolist() if not assets.empty else [""])
    if pick:
        st.button(f"Run the brief for {pick}", type="primary", on_click=_set_question,
                  args=(f"What is wrong with {pick}, how long do I have, and what should I do?", True))
        case = st.session_state.get("case") or {}
        if (case.get("asset") or {}).get("asset_id") == pick:
            st.markdown(f'<div class="verdict {verdict_tone(case)}"><h2>{html.escape(verdict_headline(case))}</h2>'
                        f'<div class="meta">{html.escape(verdict_meta(case))}</div></div>', unsafe_allow_html=True)
            st.caption("Open the Decision brief tab for the evidence and the sign-off.")
    if st.button("Recompute the triage now (about a minute)", key="triage_recompute"):
        bar = st.progress(0.0, text="Running the agent graph on every active engine")
        save_triage(build_triage(progress=lambda n, total: bar.progress(n / total, text=f"{n} of {total} engines")))
        st.rerun()


def _trace_page() -> None:
    case = st.session_state.get("case")
    result = st.session_state.get("result") or {}
    if not case:
        st.caption("Run a question on the Decision brief tab to see a trace.")
        return
    events = case.get("trace") or load_trace_file(ROOT / (result.get("trace_file") or ""))
    if not events:
        st.caption("No trace events.")
        return
    rows = trace_rows(events)
    sent_back = sum(r["status"] == "needs revision" for r in rows)
    st.write(f"Run `{result.get('run_id', case.get('case_id'))}` · {result.get('duration_s', '')}s · "
             f"{len(rows)} steps · sent back for revision {sent_back}×")
    st.caption("Every step the agents and gates took, in order. Amber rows are where a gate sent the work back. "
               "The model column names the model that actually answered; blank means the step was code.")
    esc = html.escape
    st.markdown("".join(
        f'<div class="tl-row {r["tone"]}"><span class="tl-n">{r["step"]}</span><div>'
        f'<div class="tl-head"><b>{esc(r["agent"])}</b>'
        + (f' <span class="tl-to">→ {esc(r["to"])}</span>' if r["to"] else "")
        + f' · {esc(r["action"])}<span class="tl-badge">{esc(r["status"])}</span>'
        + (f'<span class="tl-model">{esc(r["model"])}</span>' if r["model"] else "")
        + (f' <span class="tl-n">{r["seconds"]:.2f}s</span>' if r["seconds"] is not None else "")
        + f'</div><div class="tl-sum">{esc(r["summary"])}</div></div></div>'
        for r in rows), unsafe_allow_html=True)
    with st.expander("Table"):
        st.dataframe(
            pd.DataFrame([{
                "agent": e.get("agent_name"), "action": e.get("action"), "status": e.get("status"),
                "s": e.get("duration_s"), "model": e.get("model"), "out": e.get("output_summary"),
            } for e in events]),
            hide_index=True, width="stretch",
        )
    with st.expander("Full JSONL"):
        st.code("\n".join(json.dumps(e) for e in events), language="json")


def _stress_page(settings, pack: dict) -> None:
    case = st.session_state.get("case")
    if not case or not case.get("recommendation"):
        st.caption("Run a brief first. Then corrupt it here and watch the gates.")
        return
    model = CaseFile.model_validate(case)
    reference = settings.reference_time.date()
    st.markdown(f"#### Try to break the brief for {model.asset.asset_id if model.asset else 'this engine'}")
    st.write("Pick a mistake a careless or hallucinating agent could make. Cleris applies it to this brief and runs "
             "the same gates as a real run: rule checks first, then the critic's deterministic checks. No model is "
             "asked and nothing is saved.")
    clean = gate_result(model, pack, reference)
    if clean["caught_by"]:
        st.warning(f"The unmodified brief is already flagged by {clean['caught_by']}: " + "; ".join(clean["gaps"]))
    else:
        st.caption("The unmodified brief passes both gates.")
    rows = stress_rows(model, pack, reference)
    usable = [r for r in rows if r["applies"]]
    if not usable:
        st.info("None of the seeded mistakes applies to this brief.")
        return
    pick = st.selectbox("Mistake to inject", [r["what"] for r in usable], key="stress_pick")
    row = next(r for r in usable if r["what"] == pick)
    if row["caught_by"]:
        st.success(f"Caught by {row['caught_by']}. The brief goes back to the maintenance planner and never reaches "
                   "the sign-off.")
        st.markdown("\n".join(f"- {gap}" for gap in row["gaps"]))
    else:
        st.error("Not caught. This brief would reach the engineer with the mistake in it.")
    caught = sum(1 for r in usable if r["caught_by"])
    st.markdown(f"**Every mistake that applies to this brief: {caught} of {len(usable)} caught.** "
                f"{len(rows) - len(usable)} of the {len(rows)} do not apply here (for example, no part is short).")
    st.dataframe(pd.DataFrame([{
        "mistake": r["what"],
        "result": (f"caught by {r['caught_by']}" if r["caught_by"] else "NOT CAUGHT") if r["applies"] else "does not apply",
    } for r in rows]), hide_index=True, width="stretch")
    st.caption("The evaluation runs the same mistakes on 30 briefs: `uv run task eval`, section Seeded faulty briefs.")


def _help_page() -> None:
    st.markdown("### What this tool does")
    st.write("It answers three questions about one engine: what is wrong, how long it can keep flying, and what "
             "to do. Several specialist agents each look at one thing (sensor data, remaining life, fault type, "
             "parts and hangar slots). A reviewer then tries to find mistakes before you see the result. Every "
             "number is calculated by code, not written by the AI, and every statement links to its evidence.")
    st.markdown("### How to read the brief")
    st.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(HOW_TO_READ, 1)))
    st.markdown("### Colours")
    st.markdown("- **Red**: act now; the engine may wear out before the normal repair can happen.\n"
                "- **Amber**: plan soon, or inspect first because the evidence is uncertain.\n"
                "- **Green**: keep flying; routine inspection.\n"
                "- **Grey**: no recommendation (question unclear, or not enough evidence).")
    st.markdown("### Glossary")
    query = st.text_input("Search terms", placeholder="e.g. borescope, RUL, critic", key="glossary_search")
    q = (query or "").strip().lower()
    for term, meaning in GLOSSARY:
        if not q or q in term.lower() or q in meaning.lower():
            st.markdown(f"**{term}** — {meaning}")


def _pack_page(pack: dict) -> None:
    st.markdown(f"**{pack.get('asset_type')}** v{pack.get('version')}")
    st.write(pack.get("description") or "")
    st.caption(pack.get("source") or "")
    fig = engine_figure(pack, {})
    if fig:
        st.subheader("Engine layout")
        st.plotly_chart(fig, width="stretch", key="pack_diagram")
        st.caption("Schematic, not to scale. Hover a module or sensor for its name.")
    pics = photos(pack)
    if pics:
        st.subheader("Photos")
        st.caption((pack.get("images") or {}).get("disclaimer", ""))
        for start in range(0, len(pics), 3):
            for col, item in zip(st.columns(3), pics[start:start + 3]):
                col.image(item["path"], width="stretch")
                col.caption(f"**{(pack.get('components') or {}).get(item['component'], item['component'])}** · "
                            + credit(item))
    modes = pack.get("failure_modes") or {}
    st.subheader("Failure modes")
    st.table(pd.DataFrame([
        {"id": k, "label": v.get("label"), "component": v.get("component")} for k, v in modes.items()
    ]))
    st.subheader("Thresholds")
    st.json(pack.get("thresholds") or {})
    st.subheader("Data sources")
    st.json(pack.get("data_sources") or {})
    st.caption("Machine-specific knowledge lives in the asset pack. Agents read it; they do not hard-code turbofan details.")


if __name__ == "__main__":
    main()
