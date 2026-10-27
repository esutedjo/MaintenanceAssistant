"""Decision brief. Run from the repo root: uv run task ui"""

from __future__ import annotations

import html
import io
import json
import logging
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
import ui.explain  # after ui.help is fresh: it imports names from ui.help

importlib.reload(ui.explain)
importlib.reload(ui.methods)
importlib.reload(ui.stress)

from casefile.decision_log import Signer, brief_ref, derive_log, verify_rows
from casefile.schemas import CaseFile, InspectionFinding, Measurement
from data.db import load_pack
from graph.runner import execute_run
from settings import get_settings
from tools.engineering_orders import (
    describe as describe_order, read_orders, record_order, store_path as orders_path, verify_orders,
)
from tools.inspections import EXAMPLES, limit_catalog, record_finding, store_dir
from tools.writeback import approve_case, cited_cases, reject_case, signoff_anchors
from tools import status as live_status
from ui.brief import (
    CSS, GROUP_NOTES, approval_gate, asks_for_inspection, asset_context, check_summary, decision_boundaries,
    decision_log_rows, decision_rows, document_groups, driving_sensor_figure, escalation_ack_label, escalation_view,
    fault_display, fault_label, fleet_decision_targets, fmt_rul, inspection_rows, load_trace_file, md_lines, md_text,
    needs_tick, needs_urgent_reason, rul_context, sensor_trend_rows, signature_sensors, similar_case_summaries,
    status_chips, strip_links, trace_chips, trace_rows, verdict_headline, verdict_meta, verdict_tone, world_eligibility,
    zetaris_sql,
)
from ui.engine import component_probabilities, credit, engine_figure, engine_svg, photos
from ui.explain import explain
from ui.fleet import list_active_assets, load_cycles
from ui.help import (
    CLEARANCE, DISCLAIMER, DISCLAIMER_SHORT, HOW_TO_READ, INTENDED_USE, KEYFRAME_NOTE, KEYFRAMES, SECTION_HELP,
    TONE_WORDS, TOPICS, WHAT_IT_DOES, bold_html, flow_html, glossary, glossary_html, guide_html, plain_summary,
    screen_guide, search_glossary, tooltip, topic_entries,
)
from ui.methods import drivers_figure, health_index_figure, method_facts
from ui.stress import gate_result, stress_rows
from agents.intake import FLEET_QUESTION
from tools.triage import build_triage, load_triage, save_triage

ENGINE_LINK = re.compile(r"FD\d{3}-A\d{3}", re.ASCII | re.IGNORECASE)
MAX_QUESTION = 2000                 # as POST /run
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
    if st.session_state.get("help_topic"):
        _help_dialog(st.session_state.help_topic, settings, pack)
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
        _help_panel("tab", settings, pack)


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _explain(key: str) -> None:
    if st.session_state.get("explain", False):
        st.markdown(f'<div class="explain">{SECTION_HELP[key]}</div>', unsafe_allow_html=True)


def _open_help(topic: str = "") -> None:
    st.session_state.help_topic = topic or "overview"


def _close_help() -> None:
    st.session_state.help_topic = None


# Opened from the Help button or a section's "What is this?"; kept open across reruns until dismissed, so a
# question typed inside it survives a full rerun.
@st.dialog("Help: how to read the brief", width="large", on_dismiss=_close_help)
def _help_dialog(topic: str, settings, pack: dict) -> None:
    _help_panel("dlg", settings, pack, topic)


def _help_link(topic: str) -> None:
    """A small link from a section to the Help panel, opened at that section's terms."""
    st.button("What is this?", key=f"help_{topic}", type="tertiary", icon=":material/help:", on_click=_open_help,
              args=(topic,), help=f"Explain {TOPICS[topic][0].lower()} in plain words")


def _heading(text: str, topic: str, level: str = "###") -> None:
    head, link = st.columns([5, 1], vertical_alignment="bottom")
    head.markdown(f"{level} {text}")
    with link:
        _help_link(topic)


def _ask_bar(settings) -> None:
    """Question, Run and the golden cases at the top of the page, so a tablet needs no sidebar."""
    if "question" not in st.session_state:
        st.session_state.question = GOLDEN[2][1]
        # A link such as ?engine=FD001-A031 opens that engine's brief directly: an active-engine ID only, never text.
        linked = st.query_params.get("engine") or ""
        if ENGINE_LINK.fullmatch(linked):
            st.session_state.question = f"What should I do about {linked.upper()}?"
            st.session_state.run_now = True
    st.markdown(f'<div class="brand"><b>Cleris</b><span title="{html.escape(CLEARANCE)}">the MRO clearance '
                'assistant</span></div>', unsafe_allow_html=True)
    with st.container(key="ask"):
        ask, run = st.columns([5, 1], vertical_alignment="bottom")
        ask.text_input("Ask about an engine", key="question", placeholder="What should I do about FD001-A031?",
                       max_chars=MAX_QUESTION)
        clicked = run.button("Run", type="primary", width="stretch", key="run_case")
    with st.container(key="grid2-golden"):
        for col, (label, question) in zip(st.columns(len(GOLDEN)), GOLDEN):
            col.button(label, width="stretch", on_click=_set_question, args=(question, True))
    if settings.sample_mode:
        _status_strip(settings)
    else:
        _live_status_strip(settings)
    result = st.session_state.get("result") or {}
    last = f"Last run {result.get('duration_s', 0):.1f} s. " if result else ""
    note, toggle, helper = st.columns([4, 1, 1], vertical_alignment="center")
    note.caption(f"{last}Telemetry is NASA C-MAPSS (simulated); maintenance, spares and documents are "
                 f"synthetic.  \n{DISCLAIMER_SHORT}")
    toggle.toggle("Explain", value=False, key="explain",
                  help="Show a short plain-language note under each section. Help has the glossary.")
    helper.button("Help", key="help_open", icon=":material/help:", width="stretch", on_click=_open_help,
                  help="What each part of the brief means, a glossary of terms and abbreviations, pictures of the "
                       "engine, and AI help")
    if clicked or st.session_state.pop("run_now", False):
        _run(settings)


def _status_strip(settings) -> None:
    """Which background services are active: one chip each (green, amber, grey), details folded underneath. The
    checks run on a background thread and are cached (tools/status.py), so the strip never holds up the brief;
    sample mode checks nothing over the network and says what it uses instead."""
    memory = ((st.session_state.get("result") or {}).get("mode") or {}).get("context_store")
    board = live_status.current(settings, memory=memory)
    if any(item.get("summary") == "checking…" for item in board.get("components") or []):
        # First look after start: the parallel checks time out at 2.5 s, so wait that long once rather than 30 s.
        board = live_status.current(settings, wait_s=3.0, memory=memory)
    esc = html.escape
    chips = "".join(f'<span class="{esc(state)}" title="{esc(text)}"><i></i>'
                    + (f"<b>{esc(label)}</b>" if label else "") + f"{esc(text)}</span>"
                    for state, label, text in status_chips(board))
    st.markdown(f'<div class="status-strip">{chips}</div>', unsafe_allow_html=True)
    checked = board.get("checked_at")
    with st.expander("Services" + (f" · checked {checked[11:19]} UTC" if checked and board["overall"] != "sample"
                                   else ""), expanded=False):
        for item in board.get("components") or []:
            st.markdown(f"**{md_text(item['label'])}** · {md_text(item['summary'])}")
            for line in item.get("details") or []:
                st.caption(md_text(line))
        st.caption("Green: active. Amber: degraded or a fallback in use. Grey: not used in this mode or not checked "
                   "yet. Checks are read-only, take at most a few seconds on a background thread and are reused for "
                   f"{settings.status_ttl_s:.0f} s; no credentials or hosts are shown.")


@st.fragment(run_every=30)
def _live_status_strip(settings) -> None:
    """Live mode: the strip re-reads the cached checks every 30 s without re-running the page."""
    _status_strip(settings)


def _set_question(question: str, run: bool = False) -> None:
    st.session_state.question = question
    st.session_state.run_now = run


def _run(settings) -> None:
    question = (st.session_state.question or "").strip()
    if not question:
        st.error("Ask a question first.")
        return
    if len(question) > MAX_QUESTION:
        st.error(f"Questions are limited to {MAX_QUESTION} characters; name one engine, e.g. FD001-A031.")
        return
    try:
        with st.spinner("Running specialists and assurance gates…"):
            outcome = execute_run(question, settings)
    except Exception as error:
        logging.getLogger("ui").exception("run failed")
        st.error(f"The run failed ({type(error).__name__}); the server log has the details.")
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
                 "Press **Help** at the top for what each part means, a glossary and pictures of the engine.")
        return
    if case.get("degraded"):
        st.warning("Degraded: " + "; ".join(md_text(d) for d in case["degraded"]))
    terms = glossary(pack)

    def tip(text) -> str:
        return tooltip(text, terms)

    tone = verdict_tone(case)
    st.markdown(
        f'<div class="verdict {tone}"><h2>{tip(verdict_headline(case))}</h2>'
        f'<div class="meta">{tip(verdict_meta(case))}</div></div>',
        unsafe_allow_html=True,
    )
    chips = check_summary(case)
    if chips:
        st.markdown('<div class="checks">' + "".join(f'<span class="{tone}">{tip(text)}</span>'
                                                     for text, tone in chips) + "</div>", unsafe_allow_html=True)
    _explain("verdict")
    _help_link("verdict")
    ctx = asset_context(case, pack)
    _context_strip(case, ctx, tip)
    for esc in case.get("escalations") or []:
        view = escalation_view(esc)
        extra = "".join(f'<div class="esc-act">{tip(note)}</div>' for note in view["notes"])
        if view["proposed"]:
            extra += f'<div class="esc-act">Proposed: {tip(" · ".join(view["proposed"]))}</div>'
        st.markdown(f'<div class="esc {view["severity"]}"><div class="esc-title"><span class="esc-sev">'
                    f'{html.escape(view["label"])}</span>{tip(view["title"])}</div>'
                    f'<div class="esc-msg">{tip(view["message"])}</div>{extra}</div>',
                    unsafe_allow_html=True)
        if esc.get("rule") == "sb_adoption":
            _fleet_decision_form(settings, case, pack, esc)
    note = st.session_state.pop("eo_note", None)
    if note:
        st.success(md_text(note))                       # carries the typed signer name: a Markdown sink

    _explain("metrics")
    _overview_cards(case, pack)
    _plan_summary(case, tip)
    _inspection_card(case, pack, tip)
    _inspection_form(settings, case, pack)
    why = decision_boundaries(case, pack)
    if why:
        with st.container(border=True):
            _heading("Why this plan and not another", "plan", "####")
            st.markdown("\n".join(f"- {line}" for line in why))
            st.caption("Each line is a rule the planner applied, this engine's value, and the value at which the plan "
                       "would change. Computed from the planner's decision record and the asset pack thresholds.")
    summary = plain_summary(case, pack)
    if summary:
        with st.expander("In plain words", expanded=st.session_state.get("explain", False)):
            st.markdown(f'<div class="plain">{bold_html(" ".join(summary))}</div>', unsafe_allow_html=True)
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
        with st.expander(f"{eid} · {md_text(item.get('summary', ''))}{degraded}"):
            st.caption(md_text(f"{item.get('kind')} · {item.get('source')} · produced by {item.get('produced_by')}"))
            if item.get("degraded"):
                st.warning(md_text(item["degraded"]))
            _query_source(item)
            st.json(item.get("content") or {})


def _query_source(item: dict) -> None:
    """The stored Zetaris statement above the raw record, or a note when the read never left the snapshot."""
    shown = zetaris_sql(item)
    if shown is not None:
        st.code(shown["sql"], language="sql")
        seconds = shown.get("duration_s")
        clock = (f"{seconds:.2f} s" if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                 else "time not recorded")
        names = ", ".join(shown["tables"])
        st.caption(md_text(f"{names} · {clock}" if names else clock))
        st.caption("Read-only; paste into the Zetaris SQL editor to see the same rows.")
    elif str(item.get("source") or "").startswith("snapshot:"):
        st.caption("Snapshot, no Zetaris call")


def _ids_by_agent(case: dict, agent: str) -> list[str]:
    return sorted(k for k, v in (case.get("evidence") or {}).items() if v.get("produced_by") == agent)


def _overview_cards(case: dict, pack: dict) -> None:
    findings = case.get("findings") or {}
    evidence = case.get("evidence") or {}
    fault, fault_note = fault_display(case, pack)
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
                st.caption(md_text(a["note"]))
            if a:
                st.caption(f"`{a['evidence_id']}` {md_text(evidence.get(a['evidence_id'], {}).get('summary', ''))}")

    with c2.container(border=True):
        st.metric("Flights left · method B (trend)", fmt_rul(b),
                  None if "b_days" not in rc else f"≈ {rc['b_days']:.0f} days", delta_color="off", delta_arrow="off")
        with st.popover("Why?", width="stretch"):
            st.write("Combines the sensors that worsen with wear into one health score and projects when it "
                     "reaches the failure level.")
            if b and b.get("note"):
                st.caption(md_text(b["note"]))
            if "gap" in rc:
                st.caption(f"A and B are {rc['gap']:.0f} flights apart; the limit is {rc['divergence_limit']}.")
            if b:
                st.caption(f"`{b['evidence_id']}` {md_text(evidence.get(b['evidence_id'], {}).get('summary', ''))}")

    with c3.container(border=True):
        st.metric("Fault mode", fault, fault_note, delta_color="off", delta_arrow="off")
        with st.popover("Why?", width="stretch"):
            st.write("Which part of the engine is wearing, judged from the sensors that move differently for "
                     "each fault.")
            if fault_note and not fault_note.startswith("p ="):
                st.caption("No wear is detected: both remaining-life methods sit at or near the cap and agree, and "
                           "the health index is not above the fleet. This fleet has one known wear mode, so the "
                           "matcher's 1.00 for it is by construction, not a finding.")
            for m in sorted(findings.get("fault_modes") or [], key=lambda m: -m["probability"]):
                st.write(f"{fault_label(m['failure_mode'], pack)}: {m['probability']:.2f}")
            modes = findings.get("fault_modes") or []
            if modes:
                eid = modes[0]["evidence_id"]
                st.caption(f"`{eid}` {md_text(evidence.get(eid, {}).get('summary', ''))}")

    with c4.container(border=True):
        similar = findings.get("similar_cases") or []
        st.metric("Similar past cases", str(len(similar)))
        with st.popover("Why?", width="stretch"):
            st.write("Past engines removed with the same fault, from case memory, and what the shop found on each.")
            for line in similar_case_summaries(case) or ["None found (engine still in the healthy range)."]:
                st.caption(md_text(line))


def _plan_summary(case: dict, tip=None) -> None:
    rec = case.get("recommendation") or {}
    tip = tip or _esc
    if not rec:
        intent = case.get("intent") or {}
        if intent.get("needs_clarification"):
            st.info(md_text(intent.get("clarification") or "Need a clearer question."))
        if intent.get("clarification") == FLEET_QUESTION:
            triage = load_triage()
            if triage is not None:
                st.markdown("**Engines due within 7 days** (fleet triage; the Fleet tab has the full ranking)")
                _triage_table(triage, 7, "brief_triage")
        return
    esc = tip
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
    comply = "".join(
        f'<div class="plan-comply"><b>{"AD compliance" if c.get("doc_type") == "airworthiness_directive" else "Compliance"}'
        f'</b> · {esc(c.get("text") or "")}. <span class="ev-tag">{esc((c.get("evidence_ids") or [""])[0])}</span></div>'
        for c in rec.get("compliance") or [])
    st.markdown(
        f'<div class="plan-card"><div class="plan-label">Plan</div>'
        f'<div class="plan-action">{esc(rec.get("action") or "")}</div>'
        f'<div class="plan-facts">{"".join(f"<span>{f}</span>" for f in facts)}</div>'
        + (f'<div class="plan-graph">{line}</div>' if line else "") + comply + "</div>",
        unsafe_allow_html=True,
    )
    _work_package(rec.get("work_package"))


def _work_package(package: dict | None) -> None:
    """The plan on the hangar floor, folded under the plan card: one block per part of the work (engine change, shop
    visit induction, borescope), its steps with RII flags, tooling, manhours and skill, and for a lease engine what to
    verify before installation. Rows wrap on a tablet. Task references are synthetic."""
    if not package:
        return
    esc = html.escape
    sections = package.get("sections") or []
    with st.expander(f"Work package · {len(sections)} part{'s' if len(sections) != 1 else ''} · "
                     f"{package.get('manhours', 0):g} manhours · {package.get('rii', 0)} RII"):
        st.caption(md_text(package.get("note") or "Task references are synthetic."))
        for section in sections:
            st.markdown(f"**{md_text(section['title'])}** · {md_text(section['when'])}")
            meta = [f"Skill: {section['skill']}", f"{section['manhours']:g} manhours",
                    f"for {', '.join(section['for_tasks'])}"]
            if section.get("governed_by"):
                meta.append(f"limits and intervals per {section['governed_by']}")
            st.markdown(f'<div class="wp-meta">{esc(" · ".join(meta))}</div>' + "".join(
                f'<div class="wp-row"><span class="wp-ref">{esc(t["ref"])}</span><span>{esc(t["title"])}</span>'
                + ('<span class="wp-rii" title="Required inspection item: inspected by someone who did not do the '
                   'work">RII</span>' if t.get("rii") else "")
                + f'<span class="wp-h">{t["manhours"]:g} h</span></div>' for t in section.get("tasks") or []),
                unsafe_allow_html=True)
            if section.get("tooling"):
                st.caption("Tooling: " + md_text("; ".join(section["tooling"])))
            if section.get("verify_before_installation"):
                st.markdown("**To verify before installation** (Cleris has no data on these)")
                st.markdown("\n".join(f"- {md_text(item)}" for item in section["verify_before_installation"]))


DECISIONS = {"decline": "Decline: the plan follows the manual", "adopt": "Adopt: the plan follows the service bulletin"}


def _fleet_decision_form(settings, case: dict, pack: dict, esc: dict) -> None:
    """On the sb_adoption note: engineering records adopt or decline once for the whole fleet (an engineering order,
    signed, append-only and hash-chained in engineering_orders.jsonl); later briefs for every engine of that fleet
    cite it instead of raising the note. Not exposed through the API."""
    for target in fleet_decision_targets({**case, "escalations": [esc]}):
        doc_id, fleet = target["doc_id"], target["fleet"]
        key = f"eo_{fleet}_{doc_id}"
        with st.expander(f"Record fleet decision on {md_text(doc_id)} for {md_text(fleet)} (engineering order)"):
            st.caption(f"Engineering decides once for fleet {fleet}: later briefs for every {fleet} engine cite this "
                       "engineering order instead of raising this note. Stored signed, append-only and hash-chained "
                       f"in {orders_path(settings).name}; a later order for the same bulletin supersedes it.")
            choice = st.radio("Decision", list(DECISIONS), format_func=DECISIONS.get, key=f"{key}_decision")
            with st.container(key=f"grid2-{key}"):
                c1, c2 = st.columns(2)
            name = c1.text_input("Signed by", key=f"{key}_name", placeholder="Your name (required)", max_chars=120)
            reference = c2.text_input("Reference (optional)", key=f"{key}_ref", max_chars=120,
                                      placeholder="e.g. your EO number or meeting minute")
            if st.button("Record fleet decision", key=f"{key}_record", disabled=not (name or "").strip(),
                         width="stretch"):
                try:
                    row = record_order(settings, fleet=fleet, doc_id=doc_id, decision=choice,
                                       signer=Signer(name=name), pack=pack, reference=reference)
                except ValueError as error:
                    st.error(f"Not recorded: {md_text(error)}")
                    return
                _, chain = verify_orders(read_orders(settings))
                st.session_state.eo_note = (f"Recorded engineering order {describe_order(row)} ({chain}). Re-ran "
                                            "the brief.")
                st.session_state.run_now = True
                st.rerun()


def _inspection_card(case: dict, pack: dict, tip=None) -> None:
    """The inspection findings this brief read: result chips, how each was classified, the evidence behind it."""
    note = st.session_state.pop("inspection_note", None)
    if note:
        st.success(note)
    rows = inspection_rows(case, pack)
    if not rows:
        return
    tip, esc = tip or _esc, html.escape
    items = []
    for r in rows:
        chips = f'<span class="ins-chip {r["tone"]}">{esc(r["result"])}</span>'
        if r["inspector_classified"]:
            chips += '<span class="ins-chip">inspector-classified</span>'
        if r["example"]:
            chips += '<span class="ins-chip flag">example data</span>'
        if not r["current"]:
            chips += f'<span class="ins-chip">superseded by {esc(r["superseded_by"] or "")}</span>'
        head = (f"{r['kind']} · {r['location'] or r['module']} · cycle {r['cycle']} · {r['date']} · "
                f"{r['inspector']}")
        tags = "".join(f'<span class="ev-tag">{esc(i)}</span>' for i in (r["evidence_id"], r["limit_evidence_id"]) if i)
        photo = f" Photo stored at {r['photo']} (not analysed by AI)." if r["photo"] else ""
        items.append(f'<div class="ins-row{"" if r["current"] else " old"}">{chips}<span>{tip(head)}</span>{tags}'
                     f'<div class="ins-what">{esc(r["observation"])} {tip(r["basis"])}.{esc(photo)}</div></div>')
    st.markdown('<div class="plan-card"><div class="plan-label">Inspection findings</div>' + "".join(items)
                + "</div>", unsafe_allow_html=True)


INSPECTION_RESULTS = {"": "Not stated", "within": "Within limits", "at_limit": "At the limit", "beyond": "Beyond limits"}


def _ins_key(name: str, asset: str) -> str:
    return f"ins_{name}_{asset}"


def _load_example(which: str, asset: str, cycle: int, on) -> None:
    """Fill the form with a synthetic HPC borescope result (labelled example data when stored)."""
    example = EXAMPLES[which]
    values = {"kind": example["kind"], "module": example["module"], "location": example["location"],
              "observation": example["observation"], "parameter": example["measurement"]["parameter"],
              "value": float(example["measurement"]["value"]), "stated": "", "inspector": "Example inspector",
              "date": on, "cycle": cycle, "example": which}
    for name, value in values.items():
        st.session_state[_ins_key(name, asset)] = value


def _inspection_form(settings, case: dict, pack: dict) -> None:
    """Where an inspection's result comes back in: open when the verdict asks for an inspection, folded otherwise.
    The finding is stored append-only for the engine and the brief is re-run with it."""
    asset = (case.get("asset") or {}).get("asset_id")
    if not asset or not case.get("recommendation"):
        return
    spec = pack.get("inspections") or {}
    kinds, modules = spec.get("kinds") or {}, spec.get("modules") or {}
    reference = settings.reference_time.date()
    last = int((asset_context(case, pack).get("profile") or {}).get("last_cycle") or 0)
    key = lambda name: _ins_key(name, asset)  # noqa: E731
    for name, value in {"date": reference, "cycle": last, "stated": "", "value": 0.0}.items():
        st.session_state.setdefault(key(name), value)
    asked = asks_for_inspection(case)
    with st.expander("Add inspection finding" + (": the brief asks for an inspection" if asked else ""),
                     expanded=asked):
        st.caption("What the inspector found (borescope, visual, oil analysis, chip detector, NDT). Code compares a "
                   "measurement with the manual's limit; without one, your result stands and is marked "
                   "inspector-classified. The finding is stored for this engine, never edited, and every later brief "
                   "for it reads it.")
        _help_link("inspection")
        ex1, ex2 = st.columns(2)
        ex1.button("Load example finding: within limits", key=key("ex_within"), on_click=_load_example,
                   args=("within", asset, last, reference), width="stretch")
        ex2.button("Load example finding: beyond limits", key=key("ex_beyond"), on_click=_load_example,
                   args=("beyond", asset, last, reference), width="stretch")
        if st.session_state.get(key("example")):
            st.info("Example data loaded: a synthetic HPC borescope result for the demo, not a real inspection. It is "
                    "stored marked as example data.")
        with st.container(key=f"grid2-ins-what-{asset}"):
            c1, c2, c3 = st.columns(3)
        kind = c1.selectbox("Inspection", list(kinds), format_func=kinds.get, key=key("kind"))
        module = c2.selectbox("Module", list(modules), format_func=modules.get, key=key("module"))
        location = c3.text_input("Location", key=key("location"), placeholder="e.g. HPC stage 3", max_chars=120)
        observation = st.text_area("What the inspector saw", key=key("observation"), height=80, max_chars=2000)
        table = limit_catalog(pack, reference, (case.get("asset") or {}).get("variant")).get((kind, module))
        rows = (table or {}).get("rows") or []
        names = {r["id"]: (f"{r['id']} · {r['name']} ({r['unit']}): serviceable up to {r['serviceable']:g}, "
                           f"reject above {r['reject']:g}") for r in rows}
        if st.session_state.get(key("parameter")) not in ("", *names):
            st.session_state[key("parameter")] = ""
        with st.container(key=f"grid2-ins-measure-{asset}"):
            m1, m2 = st.columns([3, 1])
        parameter = m1.selectbox(
            f"Measured, against {table['reference']} {table['heading']} (synthetic)" if table else
            "Measured (no limit table covers this inspection and module)", ["", *names],
            format_func=lambda o: names.get(o, "No measurement"), key=key("parameter"))
        row = next((r for r in rows if r["id"] == parameter), None)
        value = m2.number_input(f"Value ({row['unit']})" if row else "Value", min_value=0.0, step=0.01, format="%.2f",
                                key=key("value"), disabled=row is None)
        stated = st.radio("Inspector's result" + ("" if row else " (needed without a measurement)"),
                          list(INSPECTION_RESULTS), format_func=INSPECTION_RESULTS.get, horizontal=True,
                          key=key("stated"))
        with st.container(key=f"grid2-ins-who-{asset}"):
            w1, w2, w3 = st.columns(3)
        inspector = w1.text_input("Inspector", key=key("inspector"), placeholder="Name (required)", max_chars=120)
        # No later than the reference date or the engine's last cycle: a typo (9800 for 98) would otherwise be held
        # back as implausible by the data agent.
        on = w2.date_input("Inspection date (UTC)", key=key("date"), max_value=reference)
        cycle = w3.number_input("Engine cycle at inspection", min_value=0, max_value=max(last, 0), step=1,
                                key=key("cycle"))
        photo = st.file_uploader("Photo (optional; stored with the finding, not analysed by AI)",
                                 type=["jpg", "jpeg", "png"], key=key("photo"))
        ready = bool((inspector or "").strip() and (observation or "").strip() and (row or stated))
        if not ready:
            st.caption("To record: the inspector's name, what was seen, and a measurement or the inspector's result.")
        if st.button("Record finding and re-run the brief", type="primary", key=key("record"), disabled=not ready,
                     width="stretch"):
            try:
                finding = InspectionFinding(
                    asset_id=asset, kind=kind, module=module, location=location or "", observation=observation,
                    measurement=Measurement(parameter=row["id"], value=float(value), unit=row["unit"]) if row else None,
                    stated_result=stated or None, inspector=inspector, inspected_on=on, cycle=int(cycle),
                    example=bool(st.session_state.get(key("example"))))
                if photo is not None:
                    data = photo.getvalue()
                    try:                                    # an image, not whatever bytes were uploaded
                        from PIL import Image
                        Image.open(io.BytesIO(data)).verify()
                    except Exception:
                        raise ValueError("the photo is not a readable JPEG or PNG image") from None
                    path = store_dir(settings) / "photos" / f"{finding.finding_id}{Path(photo.name).suffix.lower()}"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else Path("photos") / path.name
                    finding = finding.model_copy(update={"photo": shown.as_posix()})
                stored = record_finding(settings, finding)
            except ValueError as error:
                st.error(f"Not recorded: {md_text(error)}")
                return
            for name in [k for k in st.session_state if str(k).startswith("ins_") and str(k).endswith(f"_{asset}")]:
                del st.session_state[name]
            st.session_state.inspection_note = (f"Recorded inspection finding {finding.finding_id} for {asset} in "
                                                f"{stored.name} and re-ran the brief with it.")
            st.session_state.run_now = True
            st.rerun()


def _details(case: dict, ctx: dict, pack: dict) -> None:
    st.divider()
    _heading("Details", "details")
    st.caption("Drill down: each tab shows the working and the evidence records behind one part of the brief.")
    evidence_count = len(case.get("evidence") or {})
    docs = (case.get("findings") or {}).get("documents") or []
    entries, stored = _log_entries(case)
    tabs = st.tabs(["Remaining life", "Fault", f"Plan and documents ({len(docs)})", "History",
                    f"Checks and evidence ({evidence_count})", f"Decision log ({len(entries)})"])
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
    with tabs[5]:
        _tab_decision_log(entries, stored, case)


def _log_entries(case: dict) -> tuple[list[dict], dict | None]:
    """The stored log once the engineer has signed this case; before that, the log derived from the case file."""
    record = (st.session_state.get("decision") or {}).get("record") or {}
    if record.get("case_id") == case.get("case_id") and record.get("decision_log"):
        return record["decision_log"]["entries"], record["decision_log"]
    return [e.model_dump(mode="json") for e in derive_log(CaseFile.model_validate(case))], None


def _tab_decision_log(entries: list[dict], stored: dict | None, case: dict) -> None:
    st.caption("Who decided what, in order: agents propose, gates accept or send the work back, the model raises "
               "challenges that code confirms or refutes, and the engineer signs. Read from the case file; the "
               "sign-off is added when you decide.")
    if not entries:
        st.caption("No decisions recorded for this case.")
        return
    st.markdown("".join(_log_row_html(r) for r in decision_log_rows(entries)), unsafe_allow_html=True)
    if stored:
        # The head hash is also anchored in the run's trace and the case-memory record; both must be in the chain.
        anchors = signoff_anchors(case.get("case_id") or "", entries[-1].get("run_id") or "", get_settings())
        intact, note = verify_rows(entries, stored.get("head_hash"), anchors)
        (st.caption if intact else st.error)(f"Stored at `{stored['path']}` · {note}. The draft work-order record "
                                             "carries the same entries.")
    else:
        st.caption("Not stored yet. Approve or reject writes this log, hash-chained, to `.state/decision_logs/` and "
                   "attaches it to the work-order record.")
    with st.expander("Full JSONL"):
        st.code("\n".join(json.dumps(e) for e in entries), language="json")


def _log_row_html(r: dict) -> str:
    esc = html.escape
    meta = [f"round {r['round']}"] if r["round"] else []
    evidence = "".join(f'<span class="ev-tag">{esc(i)}</span>' for i in r["evidence_ids"])
    more = [*r["details"], *([f"hash {r['hash']}"] if r["hash"] else [])]
    return (f'<div class="dl-row {r["tone"]}"><span class="tl-n">{r["step"]}</span><div>'
            f'<div class="dl-head"><span class="dl-chip {esc(r["kind"])}">{esc(r["chip"])}</span>'
            f'<b>{esc(r["actor"])}</b><span>{esc(r["decision"])}</span>'
            + "".join(f'<span class="dl-meta">{esc(m)}</span>' for m in [*meta, r["time"]])
            + f'</div><div class="tl-sum">{esc(r["rationale"])}</div>'
            + (f'<div class="dl-ev">{evidence}</div>' if evidence else "")
            + "".join(f'<div class="dl-more">{esc(line)}</div>' for line in more) + "</div></div>")


DOC_TYPE = {"airworthiness_directive": "Airworthiness directive", "service_bulletin": "Service bulletin",
            "manual": "Maintenance manual", "procedure": "Internal procedure"}


def _tab_documents(case: dict, pack: dict) -> None:
    docs = (case.get("findings") or {}).get("documents") or []
    evidence = case.get("evidence") or {}
    order = " > ".join(DOC_TYPE.get(t, t) for t in (pack.get("documents") or {}).get("precedence") or [])
    st.caption(f"Sections that apply to this engine and finding, grouped by what they mean for compliance; highest "
               f"precedence first within a group ({order}). Only revisions in effect on the reference date are used. "
               "All documents are SYNTHETIC.")
    if not docs:
        st.info("No document applies to this case.")
        return
    for key, title, group in document_groups(case):
        st.markdown(f"##### {title} ({len(group)})")
        st.caption(GROUP_NOTES[key])
        for d in group:
            _document_card(d, evidence)


def _document_card(d: dict, evidence: dict) -> None:
    ev = evidence.get(d["evidence_id"]) or {}
    content = ev.get("content") or {}
    kind = DOC_TYPE.get(d["doc_type"], d["doc_type"])
    with st.container(border=True):
        badge = ("**MANDATORY NOW** · " if d.get("mandatory") else
                 "**RECOMMENDED** · " if d.get("group") == "recommended" else "")
        tags = " ".join(f"`{i}`" for i in (d["evidence_id"], d.get("decision_evidence_id")) if i)
        st.markdown(f"{badge}**{md_text(d['doc_id'])} {md_text(d['section'])}: {md_text(d['heading'])}**  \n"
                    f"{kind} · revision {md_text(d['revision'])} · effective {md_text(d['effective_date'])} · {tags}")
        if d.get("mandatory"):
            st.error(md_text(f"Required: {d.get('action')} (FMEA task {d.get('required_task')}, within "
                             f"{d.get('within_cycles')} cycles). Applies because {content.get('mandate_reason')}."))
        elif d.get("group_reason"):
            show = st.info if d.get("group") == "recommended" or d.get("fleet_decision") else st.caption
            show(md_text(d["group_reason"][:1].upper() + d["group_reason"][1:] + "."))
        st.markdown(md_lines(content.get("text", "")))
        st.caption(md_text(f"Relevance to this finding {d['relevance']:.2f} · source {ev.get('source', '')}"))
    retrieval = next((e for e in evidence.values()
                      if e.get("produced_by") == "knowledge" and e.get("kind") == "query"), None)
    if retrieval and retrieval["content"].get("excluded"):
        st.markdown("**Not used**")
        for item in retrieval["content"]["excluded"]:
            st.caption(md_text(f"{item['chunk']}: {item['reason']}"))


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
        st.write(f"{history['work_orders']} work orders ({md_text(by_type) or 'none'})")
        last = history.get("last_inspection")
        if last:
            st.markdown(f"Last inspection **{md_text(last['wo_id'])}** at cycle {md_text(last['cycle'])} "
                        f"({md_text((last.get('date') or '')[:10])}), severity **{md_text(last['severity'])}**: "
                        f"{md_text(last['note'])}")
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
        st.markdown("**Similar past cases: what the shop found**")
        for line in similar:
            st.markdown(f"- {md_text(line)}")
        st.caption("From the removal work orders in case memory (synthetic CMMS records); the shop's own words, in "
                   "sentence case.")
    ids = [x["id"] for x in (history, schedule, slots) if x]
    _evidence_list(case, ids + ((case.get("findings") or {}).get("similar_cases") or []))


def _tab_checks(case: dict) -> None:
    _decision_path(case)
    for esc in case.get("escalations") or []:
        st.markdown(f"**{esc.get('severity', '').upper()} · {esc.get('rule', '').replace('_', ' ')}**: "
                    f"{md_text(esc.get('message', ''))} "
                    + " ".join(f"`{i}`" for i in dict.fromkeys(esc.get("evidence_ids") or [])))
    chips = trace_chips(case.get("trace") or [])
    if chips:
        st.caption("Trace · " + " → ".join(chips))
        st.caption("Full step-by-step log in the Trace tab at the top.")


def _context_strip(case: dict, ctx: dict, tip=None) -> None:
    profile, schedule, slots = ctx.get("profile"), ctx.get("schedule"), ctx.get("slots")
    tip = tip or _esc
    result = st.session_state.get("result") or {}
    mode = result.get("mode") or {}
    items = []
    if profile:
        items.append(f"<b>{tip(profile['asset_id'])}</b> {tip(profile['esn'])} · {tip(profile['dataset'])} · "
                     f"{tip(profile['status'])}")
        items.append(f"cycle <b>{tip(profile['last_cycle'])}</b> on {tip(profile['last_seen'])}")
    if schedule:
        items.append(f"<b>{tip(schedule['cycles_next_7_days'])}</b> cycles planned in 7 d, "
                     f"<b>{tip(schedule['cycles_next_30_days'])}</b> in 30 d")
    if slots and slots.get("first_free"):
        items.append(f"first free slot <b>{tip(slots['first_free'])}</b>")
    if mode:
        items.append(f"data {tip(mode.get('data'))} · memory {tip(mode.get('context_store'))}")
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
               "then Nemotron through Nightshift (live mode) or a recorded Nemotron reply to the same prompt (sample "
               "mode, labelled recorded with its capture date). Code drops model notes it can show are false.")


def _plan(rec: dict) -> None:
    if not rec:
        st.caption("No plan for this case.")
        return
    _explain("plan")
    st.markdown(f"**{md_text(rec.get('action') or '')}**")
    st.markdown("**Why**")
    for claim in rec.get("rationale") or []:
        tags = " ".join(f"`{i}`" for i in claim.get("evidence_ids") or [])
        st.markdown(f"- {md_text(claim.get('text', ''))} {tags}")
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
        with st.expander(f"{eid} · {item.get('kind')} · {md_text(item.get('summary', ''))}{degraded}"):
            st.caption(md_text(f"{item.get('source')} · {item.get('produced_by')}"))
            if item.get("degraded"):
                st.warning(md_text(item["degraded"]))
            st.json(item.get("content") or {})


def _sign_off(settings, case: dict) -> None:
    decision = st.session_state.get("decision")
    if decision:
        rec = decision["record"]
        signed = md_text((rec.get("signed_by") or {}).get("name"))
        if decision["kind"] == "approved":
            where = "CMMS" if rec.get("backend") == "cmms" else md_text(rec.get("path", "local file"))
            st.success(f"DRAFT work order **{md_text(rec['wo_id'])}** written to {where}, signed by {signed}. It goes "
                       "to your organisation's own release process; it is not a certificate of release to service.")
        else:
            st.info(f"Rejected {md_text(rec['wo_id'])}, signed by {signed}. No work order drafted; comment stored "
                    "with the case.")
        if rec.get("cmms_error"):
            st.warning(f"CMMS insert failed ({rec['cmms_error']}); saved locally instead.")
        log = rec.get("decision_log") or {}
        if log:
            st.caption(f"Decision log: {len(log.get('entries') or [])} entries, hash-chained, stored at "
                       f"`{log.get('path')}` and attached to the work-order record (Details, Decision log tab).")
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
                st.error("Gaps: " + "; ".join(md_text(g) for g in dict.fromkeys(gaps)))
        return
    _heading("Your decision", "signoff")
    _explain("signoff")
    model = CaseFile.model_validate(case)
    with st.container(key="grid2-signer"):
        name_col, role_col, licence_col = st.columns(3)
    name = name_col.text_input("Signed by", key="signoff_name", placeholder="Your name (required)",
                               help="Required. Recorded with the time (UTC) and the brief you sign.")
    role = role_col.text_input("Role (optional)", key="signoff_role", placeholder="e.g. B1 engineer")
    licence = licence_col.text_input("Licence reference (optional)", key="signoff_licence")
    comment = st.text_area("Comment (optional)", key="signoff_comment", height=70)
    cited = cited_cases(model)
    feedback = {}
    if cited:
        with st.expander(f"Rate the {len(cited)} cited past case{'s' if len(cited) != 1 else ''} (optional)"):
            feedback = _case_feedback_form(model)
    escalations = case.get("escalations") or []
    # Urgent, warning and mandatory escalations get a tick; info ones are notes the log records as seen.
    to_tick = [(i, esc) for i, esc in enumerate(escalations) if needs_tick(esc)]
    notes = [esc for esc in escalations if not needs_tick(esc)]
    ticked = []
    if to_tick:
        st.markdown(f"**Before you approve, confirm you have read the {len(to_tick)} "
                    f"escalation{'s' if len(to_tick) != 1 else ''} above**")
        # Keyed by case so a tick never carries over to the next brief.
        ticked = [st.checkbox(escalation_ack_label(esc), key=f"ack_{model.case_id}_{i}") for i, esc in to_tick]
    if notes:
        st.caption("Notes, no tick needed: " + "; ".join(escalation_view(esc)["title"] for esc in notes)
                   + ". Your sign-off records them as noted.")
    reason = ""
    if needs_urgent_reason(escalations):
        reason = st.text_input("Why approve despite the urgent escalation?", key=f"urgent_reason_{model.case_id}",
                               placeholder="Required to approve, e.g. lease engine fitted tonight, parts expedited",
                               help="Stored with your sign-off in the decision log. Reject needs no reason.")
    sha = brief_ref(model).recommendation_sha256 or ""
    st.caption(f"You sign brief `{model.case_id}` · recommendation fingerprint `{sha[:12]}`. Approval drafts a work "
               "order for your organisation's own release process; it is not a certificate of release to service.")
    can_approve, missing = approval_gate(name, [esc for _, esc in to_tick], ticked, reason)
    if missing:
        st.caption(missing)
    unsigned = not (name or "").strip()
    read = [i for (i, _), tick in zip(to_tick, ticked) if tick]
    left, right = st.columns(2)
    if left.button("Approve and draft work order", type="primary", key="approve_case", width="stretch",
                   disabled=not can_approve):
        try:
            signer = Signer(name=name, role=role, licence_ref=licence)
            record = approve_case(model, settings, comment, case_feedback=feedback, signer=signer, acknowledged=read,
                                  reason=reason)
        except ValueError as error:          # e.g. the case was signed already, from another session
            st.error(f"Not signed: {md_text(error)}")
            return
        st.session_state.decision = {"kind": "approved", "record": record}
        st.session_state.case = {**case, "status": "approved"}
        st.rerun()
    if right.button("Reject", key="reject_case", width="stretch", disabled=unsigned):
        try:
            signer = Signer(name=name, role=role, licence_ref=licence)
            record = reject_case(model, settings, comment, case_feedback=feedback, signer=signer, acknowledged=read)
        except ValueError as error:
            st.error(f"Not signed: {md_text(error)}")
            return
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
        choice = st.radio(f"{item['evidence_id']} · {md_text(item['summary'][:110])}",
                          ["Follow my decision", *FEEDBACK_LABELS.values()], horizontal=True,
                          key=f"fb_{item['memory_id']}")
        if choice != "Follow my decision":
            out[item["memory_id"]] = next(k for k, v in FEEDBACK_LABELS.items() if v == choice)
    return out


# The triage keeps its decision categories (the eval scores them); the healthy one reads as the brief words it.
DECISION_LABELS = {"keep flying": "no action before the next scheduled inspection"}
TRIAGE_COLUMNS = {"asset_id": "engine", "decision": "decision", "deadline": "deadline", "days_to_deadline": "days left",
                  "lower_bound_cycles": "bound (cycles)", "fault_mode": "fault", "escalations": "escalations"}


def _triage_table(triage: pd.DataFrame, due_days: int | None, key: str) -> pd.DataFrame:
    shown = triage if due_days is None else triage[triage["days_to_deadline"] <= due_days]
    pack = load_pack()
    table = shown.assign(fault_mode=shown["fault_mode"].map(lambda m: fault_label(m, pack) if m else ""),
                         decision=shown["decision"].map(lambda d: DECISION_LABELS.get(d, d)))
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
            f'<span class="{"flag" if d != "keep flying" else "ok"}">{int(counts.get(d, 0))} '
            f'{DECISION_LABELS.get(d, d)}</span>'
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
    calls = [r for r in rows if r["call"]]
    st.write(f"Run `{result.get('run_id', case.get('case_id'))}` · {result.get('duration_s', '')}s · "
             f"{len(rows) - len(calls)} steps · {len(calls)} Zetaris and context-store calls · sent back for "
             f"revision {sent_back}×")
    st.caption("Every step the agents and gates took, in order, with one line per Zetaris query (zetaris query) and "
               "per World Model or H-MEM call (context call) the step made. Amber rows are where a gate sent the work "
               "back or a fallback was used. The model column names the model that answered, \"recorded reply\" "
               "when sample mode replayed a captured one; blank means the step was code.")
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
        st.warning(f"The unmodified brief is already flagged by {clean['caught_by']}: "
                   + "; ".join(md_text(g) for g in clean["gaps"]))
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
        st.markdown("\n".join(f"- {md_text(gap)}" for gap in row["gaps"]))
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


def _help_panel(prefix: str, settings, pack: dict, topic: str | None = None) -> None:
    """The help shown in the Help dialog (prefix "dlg") and on the Help tab (prefix "tab"); widget keys differ so
    both can be on the page at once."""
    case = st.session_state.get("case")
    terms = glossary(pack)
    if topic in TOPICS:
        title, note, _ = TOPICS[topic]
        st.markdown(f"**{title}.** {SECTION_HELP[note]}")
        st.markdown(glossary_html(topic_entries(topic, terms)), unsafe_allow_html=True)
        st.caption("The tabs below explain the whole screen, every term, the engine, and let you ask a question.")
    screen, words, engine, flow, ask = st.tabs(["This screen", "Glossary", "Engine and photos", "How Cleris works",
                                                "Ask Cleris"])
    with screen:
        st.markdown(f'<div class="notice">{html.escape(DISCLAIMER)}<br><br><b>Intended use.</b> '
                    f'{html.escape(INTENDED_USE)}</div>', unsafe_allow_html=True)
        st.markdown(f"**Cleris, the MRO clearance assistant.** {CLEARANCE}")
        st.write(WHAT_IT_DOES)
        summary = plain_summary(case, pack) if case else []
        if summary:
            st.markdown("**This engine in plain words**")
            st.markdown(f'<div class="plain">{bold_html(" ".join(summary))}</div>', unsafe_allow_html=True)
        st.markdown("**What each part of the screen tells you**" + (" (worded from the open brief)" if case else ""))
        st.markdown(guide_html(screen_guide(case, pack)), unsafe_allow_html=True)
        st.markdown("**How to read the brief**")
        st.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(HOW_TO_READ, 1)))
        st.markdown("**Colours**")
        st.markdown("\n".join(f"- {line}" for line in TONE_WORDS.values()))
    with words:
        query = st.text_input("Search terms and abbreviations", key=f"{prefix}_glossary_search",
                              placeholder="e.g. RUL, HPC, borescope, certificate of release to service")
        found = search_glossary(query, terms)
        st.caption(f"{len(found)} of {len(terms)} terms. Sensor entries come from the asset pack. In the brief, "
                   "abbreviations with a dotted underline show their meaning when you hover over them.")
        st.markdown(glossary_html(found), unsafe_allow_html=True)
    with engine:
        svg = engine_svg(pack)
        if svg:
            st.image(svg, width="stretch")
            st.caption("Air flows left to right: the fan pushes most of it around the core; the compressors (LPC, "
                       "HPC) squeeze the rest, it burns in the combustor, and the turbines (HPT, LPT) take power out "
                       "to drive the compressors and the fan. Drawn from the asset pack.")
        _help_photos(pack)
    with flow:
        st.markdown(flow_html(), unsafe_allow_html=True)
        if settings.sample_mode:
            st.caption("In sample mode step 3 reads the saved snapshot and step 6 runs the critic's code checks only; "
                       "nothing leaves this machine.")
        by_file = {Path(p["path"]).name: p for p in photos(pack)}
        for col, (file, caption) in zip(st.columns(len(KEYFRAMES)), KEYFRAMES):
            if file in by_file:
                col.image(by_file[file]["path"], width="stretch")
                col.caption(f"{caption} Photo: {credit(by_file[file]).split('. Photo: ', 1)[-1]}")
        st.caption(KEYFRAME_NOTE)
    with ask:
        _ask_cleris(prefix, settings, pack, case)


def _help_photos(pack: dict) -> None:
    """One illustrative photo of the whole engine and of each part a fault mode wears."""
    pics = photos(pack)
    wanted = ["engine", *dict.fromkeys(m.get("component") for m in (pack.get("failure_modes") or {}).values())]
    chosen = [p for p in (next((p for p in pics if p["component"] == c), None) for c in wanted) if p]
    if not chosen:
        return
    st.markdown("**What the parts look like**")
    names = pack.get("components") or {}
    for col, item in zip(st.columns(len(chosen)), chosen):
        col.image(item["path"], width="stretch")
        col.caption(f"**{names.get(item['component'], item['component'])}** · " + credit(item))
    st.caption((pack.get("images") or {}).get("disclaimer", ""))


def _ask_cleris(prefix: str, settings, pack: dict, case: dict | None) -> None:
    st.markdown("**Ask Cleris to explain**")
    if settings.sample_mode:
        st.caption("Sample mode: nothing leaves this machine, so questions are answered from the glossary.")
    else:
        st.caption("Your question, the glossary and the brief fields on screen (no sensor rows, no names) go to "
                   "Nemotron through Nightshift. The model explains; it does not change the decision. Code flags any "
                   "number, date or ID in the answer that the brief or glossary does not contain.")
    question = st.text_input("Your question", key=f"{prefix}_ask_question",
                             placeholder="e.g. Why are there two remaining-life numbers?")
    key = f"{prefix}_answer"
    if st.button("Explain", key=f"{prefix}_ask_go", type="primary", icon=":material/chat:"):
        with st.spinner("Looking up the glossary" if settings.sample_mode else
                        f"Asking Nemotron through Nightshift (up to {settings.critic_timeout_s:.0f} s)"):
            st.session_state[key] = {"case_id": (case or {}).get("case_id"),
                                     "answer": explain(question, case, pack, settings)}
    held = st.session_state.get(key) or {}
    answer = held.get("answer")
    if answer is None or held.get("case_id") != (case or {}).get("case_id"):
        return
    if answer.source == "model":
        with st.container(border=True):
            # Model text keeps its bullets and emphasis but never an image or a link (no fetch from the viewer).
            st.markdown(strip_links(answer.text).replace("$", "\\$"))
            st.caption(md_text(answer.label))
        if answer.unsupported:
            st.warning("Not found in the brief or the glossary, so check before relying on it: "
                       + ", ".join(md_text(t) for t in answer.unsupported))
        else:
            st.caption("Every number, date and ID in this answer appears in the brief or the glossary.")
        if answer.advice:
            st.warning("This reads as advice, which the model was told not to give. The decision is the brief's "
                       "recommendation and your sign-off: " + " / ".join(md_text(a) for a in answer.advice))
        if answer.entries:
            with st.expander("Related glossary entries"):
                st.markdown(glossary_html(answer.entries), unsafe_allow_html=True)
    else:
        st.info(answer.note)                    # code's own wording (sample mode, Nightshift did not answer, ...)
        if answer.entries:
            st.markdown(glossary_html(answer.entries), unsafe_allow_html=True)
        elif answer.note != "Type a question first.":
            st.caption("No glossary entry matches; try a single term such as RUL, borescope or escalation.")


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
