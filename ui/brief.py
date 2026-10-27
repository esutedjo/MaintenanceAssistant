"""Pure helpers for the decision brief. Streamlit rendering lives in ui/app.py so these stay testable."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import sqlglot
from sqlglot import exp

from assurance.escalation import needs_acknowledgement
from casefile.calls import CALL_ACTIONS
from casefile.shop_findings import finding_line
from casefile.wear import NOT_DETECTED, no_wear_label

CSS = """
<style>
.block-container { padding-top: 3.6rem; max-width: 1180px; }
[data-testid="stMetricValue"] { font-size: 1.7rem; }
[data-testid="stMetricValue"] p, [data-testid="stMetricLabel"] p {
  white-space: normal !important; overflow-wrap: anywhere; line-height: 1.2; }
.checks { display: flex; flex-wrap: wrap; gap: .4rem; margin: -.4rem 0 1rem 0; }
.checks span { background: #1a2332; border: 1px solid #2b3a4f; border-radius: 999px; padding: .15rem .7rem;
               font-size: .82rem; color: #cbd5e1; }
.checks span.ok { border-color: #15803d; }
.checks span.flag { border-color: #b45309; }
.esc { border: 1px solid; border-radius: 8px; padding: .55rem .9rem; margin: 0 0 .6rem 0; }
.esc.urgent { background: #2a1015; border-color: #e11d48; }
.esc.warning { background: #2a2210; border-color: #f59e0b; }
.esc.info { background: #16202e; border-color: #475569; }
.esc-title { font-weight: 700; font-size: .95rem; }
.esc-sev { font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; margin-right: .5rem; }
.esc.urgent .esc-sev { color: #fda4af; }
.esc.warning .esc-sev { color: #fcd34d; }
.esc.info .esc-sev { color: #93c5fd; }
.esc-msg { color: #e2e8f0; font-size: .92rem; margin-top: .15rem; }
.esc-act { color: #cbd5e1; font-size: .85rem; margin-top: .3rem; }
.plan-card { background: #16202e; border: 1px solid #2b3a4f; border-radius: 8px; padding: .8rem 1rem;
             margin: .4rem 0 1.2rem 0; }
.plan-label { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: #93c5fd; }
.plan-action { font-size: 1.1rem; font-weight: 600; margin: .15rem 0 .5rem 0; }
.plan-facts { display: flex; flex-wrap: wrap; gap: .3rem 1.6rem; font-size: .9rem; color: #cbd5e1; }
.plan-facts b { color: #e8eef5; }
.plan-graph { margin-top: .55rem; font-size: .88rem; color: #cbd5e1; }
.plan-comply { margin-top: .55rem; font-size: .9rem; color: #e2e8f0; border-left: 3px solid #e11d48;
               padding: .1rem 0 .1rem .6rem; }
.plan-comply b { color: #fda4af; }
/* Work package: one row per task, wrapping on a tablet; RII chips stand out. */
.wp-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: .1rem .6rem; padding: .4rem 0;
          border-top: 1px solid #1f2b3b; font-size: .92rem; }
.wp-ref { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #93c5fd; font-size: .8rem; }
.wp-rii { font-size: .72rem; letter-spacing: .05em; border-radius: 999px; padding: .02rem .5rem;
          border: 1px solid #e11d48; color: #fda4af; }
.wp-h { color: #94a3b8; font-size: .85rem; margin-left: auto; white-space: nowrap; }
.wp-meta { color: #cbd5e1; font-size: .88rem; margin: .1rem 0 .3rem 0; }
.ins-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: .2rem .6rem; padding: .45rem 0;
           border-top: 1px solid #1f2b3b; font-size: .92rem; }
.ins-row:first-of-type { border-top: none; }
.ins-row.old { opacity: .6; }
.ins-chip { font-size: .74rem; letter-spacing: .05em; text-transform: uppercase; border-radius: 999px;
            padding: .05rem .6rem; border: 1px solid #334155; color: #cbd5e1; }
.ins-chip.ok { border-color: #15803d; color: #86efac; }
.ins-chip.flag { border-color: #b45309; color: #fcd34d; }
.ins-chip.bad { border-color: #e11d48; color: #fda4af; }
.ins-what { flex: 1 1 100%; color: #cbd5e1; font-size: .88rem; }
.tl-row { display: grid; grid-template-columns: 2rem 1fr; gap: 0 .6rem; padding: .45rem .2rem;
          border-bottom: 1px solid #1f2b3b; }
.tl-n { color: #64748b; font-size: .85rem; text-align: right; padding-top: .1rem; }
.tl-head { font-size: .92rem; }
.tl-head b { color: #e8eef5; }
.tl-to { color: #93c5fd; }
.tl-badge { font-size: .72rem; border-radius: 999px; padding: .05rem .5rem; margin-left: .4rem; border: 1px solid #334155; }
.tl-row.ok .tl-badge { border-color: #15803d; color: #86efac; }
.tl-row.flag { background: #221c0e; }
.tl-row.flag .tl-badge { border-color: #b45309; color: #fcd34d; }
.tl-row.bad .tl-badge { border-color: #e11d48; color: #fda4af; }
.tl-model { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .8rem; color: #a5b4fc;
            margin-left: .4rem; }
.tl-sum { grid-column: 2; color: #cbd5e1; font-size: .86rem; }
.dl-row { display: grid; grid-template-columns: 2rem 1fr; gap: 0 .6rem; padding: .5rem .2rem;
          border-bottom: 1px solid #1f2b3b; }
.dl-row.flag { background: #221c0e; }
.dl-row.bad { background: #2a1015; }
.dl-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: .15rem .45rem; font-size: .92rem; }
.dl-head b { color: #e8eef5; }
.dl-chip { font-size: .72rem; letter-spacing: .05em; text-transform: uppercase; border-radius: 999px;
           padding: .05rem .55rem; border: 1px solid #334155; color: #cbd5e1; }
.dl-chip.agent { border-color: #3b82f6; color: #93c5fd; }
.dl-chip.gate { border-color: #0d9488; color: #5eead4; }
.dl-chip.model { border-color: #6366f1; color: #a5b4fc; }
.dl-chip.human { border-color: #15803d; color: #86efac; }
.dl-meta { color: #64748b; font-size: .82rem; }
.dl-ev { display: flex; flex-wrap: wrap; gap: .1rem .7rem; font-size: .84rem; margin-top: .15rem; }
.dl-more { color: #94a3b8; font-size: .84rem; margin-top: .15rem; overflow-wrap: anywhere; }
/* Live status strip under the ask bar: one chip per service, wraps on a tablet. */
.status-strip { display: flex; flex-wrap: wrap; gap: .3rem .45rem; margin: .1rem 0 .2rem 0; }
.status-strip span { display: inline-flex; align-items: center; gap: .35rem; background: #111a27;
                     border: 1px solid #2b3a4f; border-radius: 999px; padding: .1rem .6rem; font-size: .78rem;
                     color: #cbd5e1; white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.status-strip span b { color: #e8eef5; font-weight: 600; }
.status-strip i { width: .55rem; height: .55rem; border-radius: 999px; flex: none; background: #64748b; }
.status-strip .ok i { background: #22c55e; }
.status-strip .warn i { background: #f59e0b; }
.status-strip .warn { border-color: #b45309; }
.brand { display: flex; align-items: baseline; gap: .6rem; margin: 0 0 .2rem 0; }
.brand b { font-size: 1.2rem; }
.brand span { color: #94a3b8; font-size: .9rem; }
/* Tablet: four-up rows become 2 x 2 and the two method cards stack (phones already stack every column). */
@media (max-width: 1024px) {
  .block-container { padding-left: 1.2rem; padding-right: 1.2rem; }
  [class*="st-key-grid2"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: .6rem; }
  [class*="st-key-grid2"] [data-testid="stColumn"] { flex: 1 1 calc(50% - 1rem) !important;
                                                    min-width: calc(50% - 1rem) !important; }
  [class*="st-key-stack"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: .8rem; }
  [class*="st-key-stack"] [data-testid="stColumn"] { flex: 1 1 100% !important; min-width: 100% !important; }
  .verdict h2 { font-size: 1.2rem; }
}
.verdict { padding: 1rem 1.15rem; border-radius: 8px; margin: 0 0 1rem 0; }
.verdict h2 { margin: 0 0 .35rem 0; font-size: 1.35rem; line-height: 1.3; }
.verdict .meta { opacity: .9; font-size: .95rem; }
.verdict.urgent { background: #3a1218; border: 1px solid #e11d48; }
.verdict.warning { background: #3a2e12; border: 1px solid #f59e0b; }
.verdict.ok { background: #122a1c; border: 1px solid #22c55e; }
.verdict.hold { background: #1e2430; border: 1px solid #64748b; }
.synthetic { background: #1e293b; color: #cbd5e1; padding: .45rem .7rem; border-radius: 6px;
             font-size: .85rem; margin-bottom: .8rem; }
.escalation { color: #fda4af; font-weight: 600; }
.ev-tag { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #93c5fd; }
.context { display: flex; flex-wrap: wrap; gap: .4rem 1.4rem; font-size: .9rem; color: #cbd5e1;
           margin: -.4rem 0 1rem 0; }
.context b { color: #e8eef5; font-weight: 600; }
.plain { line-height: 1.6; font-size: 1.02rem; }
.plain-title { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: #93c5fd;
               margin-bottom: .25rem; }
.explain { color: #94a3b8; font-size: .88rem; border-left: 2px solid #334155; padding: .1rem 0 .1rem .6rem;
           margin: -.2rem 0 .8rem 0; }
/* Help: glossary tooltips on abbreviations, the screen guide, the glossary list and the step strip. */
abbr[title] { text-decoration: underline dotted #64748b; text-underline-offset: 3px; cursor: help; }
.notice { background: #1e293b; border-left: 3px solid #f59e0b; color: #cbd5e1; padding: .5rem .8rem;
          border-radius: 4px; font-size: .88rem; margin: 0 0 .9rem 0; }
.guide { display: grid; grid-template-columns: 11rem 1fr; gap: .5rem 1rem; font-size: .94rem; line-height: 1.5;
         margin: .3rem 0 1rem 0; }
.guide dt { color: #93c5fd; font-weight: 600; }
.guide dd { margin: 0; color: #e2e8f0; }
.gloss { font-size: .93rem; line-height: 1.5; }
.gloss > div { padding: .45rem 0; border-bottom: 1px solid #1f2b3b; }
.gloss b { color: #e8eef5; }
.gloss .full, .gloss .where { color: #94a3b8; }
.gloss .where { font-size: .84rem; }
.flow { display: flex; flex-wrap: wrap; gap: .5rem; margin: .4rem 0 1rem 0; }
.flow-step { flex: 1 1 8.5rem; background: #16202e; border: 1px solid #2b3a4f; border-radius: 8px;
             padding: .5rem .65rem; font-size: .85rem; color: #cbd5e1; line-height: 1.4; }
.flow-step b { display: block; color: #e8eef5; font-size: .9rem; margin: .2rem 0 .15rem 0; }
.flow-n { display: inline-block; width: 1.4rem; height: 1.4rem; border-radius: 999px; background: #1e3a5f;
          color: #93c5fd; text-align: center; font-size: .78rem; line-height: 1.4rem; }
@media (max-width: 640px) {
  .guide { grid-template-columns: 1fr; gap: .1rem; }
  .guide dd { margin-bottom: .5rem; }
}
</style>
"""


# Markdown sinks (st.markdown, st.write, st.caption, st.error/warning/info/success, expander and radio labels) render
# images and links: a stored inspector name "![x](http://host/p.png)" made the viewer's browser fetch it. Data and model
# text goes through md_text (one line) or md_lines (keeps line breaks); model answers lose images and links first.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<$])")       # "[" also breaks Streamlit's :color[...] directives
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_AUTOLINK = re.compile(r"<(?:https?|ftp|mailto|javascript):[^>]*>", re.IGNORECASE)


def md_text(value: Any) -> str:
    """Data or model text shown as literal text in Markdown: every Markdown character escaped, on one line."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(value if value is not None else "").split()))


def md_lines(value: Any) -> str:
    """Like md_text, keeping the text's line breaks (document sections)."""
    return "  \n".join(md_text(line) for line in str(value if value is not None else "").splitlines())


def strip_links(text: str) -> str:
    """Model text keeps its own formatting but never an image or a link: [label](url) and ![alt](url) become their
    label, <http://…> autolinks go."""
    return _AUTOLINK.sub("", _MD_LINK.sub(r"\1", text or ""))


STATUS_SHORT = {"mode": "Mode", "zetaris": "Zetaris", "context_service": "Context service",
                "nightshift": "Nightshift", "cmms": "CMMS"}


def status_chips(board: dict) -> list[tuple[str, str, str]]:
    """(state, label, text) per chip of the status strip (tools/status.py). Sample mode is one grey chip."""
    if board.get("overall") == "sample":
        return [("off", "", board.get("summary") or "Sample mode")]
    return [(c["state"], STATUS_SHORT.get(c["key"], c["label"]), c.get("chip") or c["summary"])
            for c in board.get("components") or []]


def sensor_label(sensor: str, pack: dict) -> str:
    meta = pack.get("sensors", {}).get(sensor, {})
    name = meta.get("name", sensor)
    unit = meta.get("unit")
    return f"{name} ({sensor}, {unit})" if unit else f"{name} ({sensor})"


def fault_label(mode: str, pack: dict) -> str:
    entry = pack.get("failure_modes", {}).get(mode) or {}
    return entry.get("label", mode.replace("_", " "))


def primary_fault(findings: dict, pack: dict) -> tuple[str, float | None]:
    modes = findings.get("fault_modes") or []
    if not modes:
        return "—", None
    top = max(modes, key=lambda m: m.get("probability") or 0)
    return fault_label(top["failure_mode"], pack), top.get("probability")


def fault_display(case: dict, pack: dict) -> tuple[str, str | None]:
    """The fault chip: (value, note under it). A healthy engine on a one-mode fleet shows "none detected" and names the
    fleet's only known wear mode instead of its by-construction probability 1.00 (casefile/wear.py)."""
    findings = case.get("findings") or {}
    quiet = no_wear_label(findings, pack, (case.get("asset") or {}).get("variant"))
    if quiet and findings.get("fault_modes"):
        return NOT_DETECTED, f"only known mode: {quiet}"
    fault, prob = primary_fault(findings, pack)
    return fault, None if prob is None else f"p = {prob:.2f}"


def fmt_rul(finding: dict | None, with_range: bool = False) -> str:
    if not finding:
        return "—"
    text = f"{finding['rul']:.0f}"
    if with_range:
        text = f"{text}  ({finding['lower']:.0f}–{finding['upper']:.0f})"
    if finding.get("at_cap"):
        text += " cap"
    return text


def verdict_tone(case: dict) -> str:
    status = case.get("status")
    if status in {"insufficient_evidence", "needs_clarification", "error"}:
        return "hold"
    if any(e.get("severity") == "urgent" for e in case.get("escalations") or []):
        return "urgent"
    rec = case.get("recommendation") or {}
    if any(e.get("severity") == "warning" for e in case.get("escalations") or []) or rec.get("confidence") == "low":
        return "warning"
    return "ok"


def verdict_headline(case: dict) -> str:
    rec = case.get("recommendation") or {}
    if rec.get("action"):
        return rec["action"]
    return {
        "needs_clarification": "Need a clearer question",
        "insufficient_evidence": "Insufficient evidence — case closed",
        "error": "Run failed",
        "rejected": "Recommendation rejected",
        "approved": "Approved",
    }.get(case.get("status") or "", "No recommendation yet")


def verdict_meta(case: dict) -> str:
    rec = case.get("recommendation") or {}
    bits = []
    if rec.get("deadline"):
        bits.append(f"deadline {rec['deadline']}")
    if rec.get("confidence"):
        bits.append(f"confidence {rec['confidence']}")
    bits.append(case.get("status", "").replace("_", " "))
    return " · ".join(bits)


def driving_sensor_figure(cycles: pd.DataFrame, drivers: list[dict], pack: dict, baseline: int = 20,
                          smooth: int = 10, limit: int = 5) -> go.Figure | None:
    """Full life of each sensor in standard deviations from the engine's own first `baseline` cycles, so
    sensors with different units share one axis."""
    sensors = [d["sensor"] for d in drivers if d.get("sensor") in cycles.columns][:limit]
    if not sensors or cycles.empty:
        return None
    frame = cycles.sort_values("cycle")
    fig = go.Figure()
    for sensor in sensors:
        values = frame[sensor].astype(float)
        base = values.iloc[:baseline]
        z = (values - base.mean()) / (base.std() or 1e-9)
        fig.add_trace(go.Scatter(x=frame["cycle"], y=z.rolling(smooth, min_periods=1).mean(),
                                 name=sensor_label(sensor, pack), mode="lines"))
    fig.add_hline(y=0, line_dash="dot", line_color="#64748b")
    fig.update_layout(
        margin=dict(l=40, r=16, t=24, b=40), height=340, legend=dict(orientation="h", y=1.15),
        xaxis_title="cycle", yaxis_title=f"sd from first {baseline} cycles ({smooth}-cycle mean)",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e8eef5"),
    )
    fig.update_xaxes(gridcolor="#1e293b")
    fig.update_yaxes(gridcolor="#1e293b")
    return fig


def find_evidence(case: dict, key: str) -> tuple[str, dict] | tuple[None, None]:
    """First evidence record whose content has `key` (e.g. "esn" for the asset profile)."""
    for eid, item in sorted((case.get("evidence") or {}).items()):
        if key in (item.get("content") or {}):
            return eid, item
    return None, None


def asset_context(case: dict, pack: dict) -> dict[str, Any]:
    """Engine identity, usage and planning facts, each with the evidence ID it came from."""
    out: dict[str, Any] = {}
    eid, item = find_evidence(case, "esn")
    if item:
        c = item["content"]
        out["profile"] = {"id": eid, "asset_id": c.get("asset_id"), "esn": c.get("esn"), "dataset": c.get("dataset"),
                          "status": c.get("fleet_status"), "last_cycle": c.get("last_cycle"),
                          "last_seen": (c.get("last_seen") or "")[:10]}
        modes = pack.get("variants", {}).get(c.get("dataset"), [])
        out["profile"]["possible_modes"] = [fault_label(m, pack) for m in modes]
    eid, item = find_evidence(case, "cycles_next_7_days")
    if item:
        out["schedule"] = {"id": eid, **{k: item["content"].get(k) for k in
                                         ("cycles_next_7_days", "cycles_next_30_days", "last_scheduled")}}
    eid, item = find_evidence(case, "first_free")
    if item:
        out["slots"] = {"id": eid, **{k: item["content"].get(k) for k in
                                      ("first_free", "days_to_first_free", "free_slots")}}
    eid, item = find_evidence(case, "work_orders")
    if item:
        out["history"] = {"id": eid, **{k: item["content"].get(k) for k in
                                        ("work_orders", "by_type", "last_inspection", "findings")}}
    eid, item = find_evidence(case, "sensors")
    if item:
        out["telemetry"] = {"id": eid, **{k: item["content"].get(k) for k in
                                          ("rows", "first_cycle", "last_cycle", "baseline_cycles", "sensors")}}
    return out


def rul_context(findings: dict, pack: dict) -> dict[str, Any]:
    """Derived readings of the two RUL methods: days at the pack's utilisation, A-B gap vs the divergence rule."""
    thresholds = pack.get("thresholds", {})
    per_day = thresholds.get("cycles_per_day") or 1
    limit = thresholds.get("rul_divergence_cycles")
    a, b = findings.get("rul_ml"), findings.get("rul_trend")
    out: dict[str, Any] = {"cycles_per_day": per_day, "divergence_limit": limit}
    if a:
        out["a_days"] = a["rul"] / per_day
        out["a_lower_days"] = a["lower"] / per_day
    if b:
        out["b_days"] = b["rul"] / per_day
    if a and b:
        gap = abs(a["rul"] - b["rul"])
        out["gap"] = gap
        out["diverged"] = limit is not None and gap > limit
    out["healthy"] = bool(a and b and (a.get("at_cap") or b.get("at_cap")) and not out.get("diverged"))
    return out


def sensor_trend_rows(telemetry: dict, top: int = 8) -> list[dict]:
    sensors = (telemetry or {}).get("sensors") or {}
    ranked = sorted(sensors.items(), key=lambda kv: -abs(kv[1].get("change_sigma") or 0))[:top]
    return [{"sensor": f"{v['name']} ({k})", "component": v["component"], "unit": v["unit"],
             "baseline": v["baseline"], "recent": v["recent"], "change (sd)": v["change_sigma"],
             "slope / cycle": v["slope_per_cycle"]} for k, v in ranked]


def signature_sensors(pack: dict, variant: str | None) -> list[str]:
    """Sensors whose degradation direction differs across the variant's failure modes."""
    modes = pack.get("variants", {}).get(variant or "", [])
    if len(modes) < 2:
        return []
    return [s for s, spec in pack.get("sensors", {}).items()
            if len({spec["direction"].get(m) for m in modes}) > 1]


ESCALATION_TITLES = {
    "document_mandate": "Mandatory document action",
    "inspection_beyond_limits": "Inspection finding beyond limits",
    "inspection_at_limit": "Inspection finding at the limit",
    "rul_below_lead_time": "The engine wears out before a normal repair can happen",
    "rul_divergence": "The two remaining-life methods disagree",
    "ambiguous_fault": "Two fault modes are too close to call",
    "data_quality": "Input data failed a plausibility check",
    "knowledge_drift": "The failure-mode table lists work the World Model does not",
    "sb_adoption": "Service bulletin needs engineering approval",
    "potentially_reportable": "Potentially reportable occurrence",
}


def escalation_view(esc: dict) -> dict[str, Any]:
    """One escalation in the engineer's words: title, message, proposed actions and fixed notes."""
    message = esc.get("message") or ""
    proposed, notes = [], []
    for action in esc.get("actions") or []:
        if action.startswith("Mandatory:"):
            notes.append(action.removeprefix("Mandatory:").strip().capitalize())
        elif action not in message:
            proposed.append(action.removeprefix("Propose ").strip())
    rule = esc.get("rule") or ""
    severity = esc.get("severity") or "warning"
    return {"severity": severity, "label": "note" if severity == "info" else severity,
            "title": ESCALATION_TITLES.get(rule, rule.replace("_", " ")), "message": message, "proposed": proposed,
            "notes": notes, "tick": needs_tick(esc)}


def needs_tick(esc: dict) -> bool:
    """Urgent, warning and mandatory-document escalations are ticked before approval; info ones are notes the
    sign-off records as seen (assurance.escalation.needs_acknowledgement)."""
    return needs_acknowledgement(esc.get("severity"), esc.get("rule"))


def escalation_ack_label(esc: dict) -> str:
    """The checkbox an engineer ticks to confirm an escalation was read before approving."""
    view = escalation_view(esc)
    return f"I have read the {view['severity']} escalation: {view['title']}. {view['message']}"


def needs_urgent_reason(escalations: list[dict]) -> bool:
    return any(e.get("severity") == "urgent" for e in escalations)


def approval_gate(signer: str, escalations: list[dict], ticked: list[bool], reason: str = "") -> tuple[bool, str]:
    """Whether Approve is enabled and, if not, what is missing. Reject needs only the signer's name. With an urgent
    escalation, Approve also needs a typed reason for approving despite it."""
    open_items = len(escalations) - sum(1 for t in ticked[:len(escalations)] if t)
    ticks = f"tick {open_items} more escalation{'s' if open_items != 1 else ''} as read"
    if not (signer or "").strip():
        return False, ("To approve or reject, type your name in Signed by"
                       + (f"; to approve, also {ticks}." if open_items else "."))
    if open_items:
        return False, f"To approve, {ticks}. Reject needs no acknowledgement."
    if needs_urgent_reason(escalations) and not (reason or "").strip():
        return False, "To approve despite the urgent escalation, say why in the box above. Reject needs no reason."
    return True, ""


def world_eligibility(case: dict) -> dict[str, Any] | None:
    """The World Model's eligible tasks for the fault next to the tasks the plan uses."""
    for eid, item in sorted((case.get("evidence") or {}).items()):
        content = item.get("content") or {}
        if item.get("produced_by") == "maint_planner" and ":world/" in (item.get("source") or "") \
                and "task_ids" in content:
            listed = list(dict.fromkeys(content.get("task_ids") or []))
            used = (case.get("recommendation") or {}).get("task_ids") or []
            return {"evidence_id": eid, "listed": listed, "used": [t for t in used if t in listed],
                    "off_graph": [t for t in used if t not in listed], "degraded": item.get("degraded")}
    return None


def check_summary(case: dict) -> list[tuple[str, str]]:
    """How the brief was checked, as (text, tone) chips: tone is ok, flag or plain."""
    verdicts = case.get("verdicts") or []
    rules = [v for v in verdicts if v.get("gate") == "rule_checks"]
    critic = [v for v in verdicts if v.get("gate") == "critic"]
    chips: list[tuple[str, str]] = []
    intent = case.get("intent") or {}
    reader = intent.get("resolved_by")
    if reader:
        by = intent["recorded"] if intent.get("recorded") else reader      # a replay names itself, with its date
        chips.append((f"engine read from free text by {by}, checked by code", "plain"))
    if rules:
        sent_back = sum(v.get("outcome") != "accept" for v in rules)
        chips.append(("rule checks passed" if not sent_back else f"rule checks sent it back {sent_back}×",
                      "ok" if rules[-1].get("outcome") == "accept" else "flag"))
    if critic:
        revisions = sum(v.get("outcome") == "revise" for v in critic)
        last = critic[-1].get("outcome") or ""
        text = f"critic {'accepted' if last == 'accept' else last}"
        if revisions:
            text += f" after {revisions} revision{'s' if revisions > 1 else ''}"
        chips.append((text, "ok" if last == "accept" else "flag"))
        models = list(dict.fromkeys(v["model"] for v in critic if v.get("model")))
        recorded = [v["recorded"] for v in critic if v.get("model") and v.get("recorded")]
        if recorded:                 # sample mode replays a captured reply; say so, with its date, so it never reads live
            chips.append((f"critic: {recorded[-1]}", "plain"))
        else:
            chips.append((f"LLM critic: {', '.join(models)}" if models else "critic: deterministic checks only",
                          "plain"))
    escalations = case.get("escalations") or []
    if escalations:
        chips.append((f"{len(escalations)} escalation{'s' if len(escalations) > 1 else ''}", "flag"))
    chips.append((f"{len(case.get('evidence') or {})} evidence records", "plain"))
    return chips


def _days(n: float) -> str:
    return f"{n:g} day" if n == 1 else f"{n:g} days"


RESULT_TONE = {"within": "ok", "at_limit": "flag", "beyond": "bad"}
RESULT_WORDS = {"within": "within limits", "at_limit": "at the limit", "beyond": "beyond limits"}


def inspection_rows(case: dict, pack: dict) -> list[dict[str, Any]]:
    """The inspection findings in the brief, newest first: what was found, the result chip, how it was classified
    (against which limit, or by the inspector), and the evidence tags behind it."""
    evidence = case.get("evidence") or {}
    spec = pack.get("inspections") or {}
    rows = []
    for item in (case.get("findings") or {}).get("inspections") or []:
        finding = ((evidence.get(item["evidence_id"]) or {}).get("content") or {}).get("finding") or {}
        measured = finding.get("measurement")
        limit = item.get("limit") or {}
        if item.get("classified_by") == "limit" and measured and limit:
            basis = (f"{limit.get('name')} {measured['value']:g} {measured['unit']} against {item.get('reference')} "
                     f"(serviceable up to {limit['serviceable']:g}, reject above {limit['reject']:g} {limit['unit']})")
        else:
            basis = f"inspector-classified: {item.get('note') or 'no limit applies'}"
        rows.append({
            "evidence_id": item["evidence_id"], "limit_evidence_id": item.get("limit_evidence_id"),
            "kind": (spec.get("kinds") or {}).get(item["kind"], item["kind"]),
            "module": (spec.get("modules") or {}).get(item["module"], item["module"]),
            "location": item.get("location") or "", "cycle": item.get("cycle"),
            "date": finding.get("inspected_on"), "inspector": finding.get("inspector"),
            "observation": finding.get("observation") or "", "result": RESULT_WORDS[item["result"]],
            "tone": RESULT_TONE[item["result"]], "inspector_classified": item.get("classified_by") == "inspector",
            "basis": basis, "current": item.get("current", True), "superseded_by": item.get("superseded_by"),
            "example": bool(finding.get("example")), "photo": finding.get("photo"),
            "interval": item.get("interval_cycles"), "action": item.get("action"),
        })
    return sorted(rows, key=lambda r: (r["current"], r["cycle"] or 0, r["evidence_id"]), reverse=True)


def asks_for_inspection(case: dict) -> bool:
    """The verdict is an inspection: the place an inspector's result comes back in."""
    from tools.triage import decision_kind
    return decision_kind((case.get("recommendation") or {}).get("action")) == "inspect"


def _inspection_boundary(item: dict, case: dict) -> str:
    """The limit the finding was compared with and the values at which the plan would change."""
    finding = (((case.get("evidence") or {}).get(item["evidence_id"]) or {}).get("content") or {}).get("finding") or {}
    measured, row, ev, ref = finding.get("measurement"), item.get("limit") or {}, item["evidence_id"], item.get("reference")
    if item.get("classified_by") != "limit" or not measured or not row:
        return (f"**{RESULT_WORDS[item['result']].capitalize()} on the inspection** because the inspector "
                f"{md_text(finding.get('inspector') or '')} classified finding {ev} so; no limit section covers it, so the "
                "plan follows the inspector's result.")
    value = f"{row['id']} {measured['value']:g} {row['unit']}"
    ok, reject = f"{row['serviceable']:g} {row['unit']}", f"{row['reject']:g} {row['unit']}"
    if item["result"] == "beyond":
        return (f"**Remove before further flight** because inspection finding {ev} measured {value}, above the reject "
                f"limit {reject} of {ref}. Up to {reject} the plan would re-inspect at the manual's interval; up to "
                f"{ok}, no removal on this finding.")
    if item["result"] == "at_limit":
        return (f"**Re-inspect within {item.get('interval_cycles')} cycles** because inspection finding {ev} measured "
                f"{value}, above the serviceable limit {ok} but not above the reject limit {reject} of {ref}. Above "
                f"{reject} the plan would remove the engine before further flight; up to {ok}, no removal.")
    return (f"**No removal on the inspection** because finding {ev} measured {value}, within the serviceable limit "
            f"{ok} of {ref}. Above {ok} the plan would re-inspect at the manual's interval; above {reject} it would "
            "remove the engine before further flight.")


def decision_boundaries(case: dict, pack: dict) -> list[str]:
    """Why this action and not another: each rule the planner applied, this engine's value, and the value at which
    the plan would change. Computed from the planner's own decision record and the asset pack thresholds."""
    findings, rec = case.get("findings") or {}, case.get("recommendation") or {}
    a, b = findings.get("rul_ml"), findings.get("rul_trend")
    evidence = (case.get("evidence") or {}).values()
    plan = next((e["content"] for e in evidence if e.get("produced_by") == "maint_planner"
                 and "rul_lower_cycles" in (e.get("content") or {})), None)
    if not rec or not a or plan is None:
        return []
    th = pack["thresholds"]
    cpd, severe, moderate, limit = (th["cycles_per_day"], th["severe_rul_cycles"], th["moderate_rul_cycles"],
                                    th["rul_divergence_cycles"])
    lower, severity, action = plan["rul_lower_cycles"], plan["severity"], rec.get("action") or ""
    gap = abs(a["rul"] - b["rul"]) if b else None
    diverge = gap is not None and gap > limit
    at_cap = [name for name, f in (("method A", a), ("method B", b)) if f and f.get("at_cap")]
    current = [i for i in findings.get("inspections") or [] if i.get("current", True)]
    worst = max(current, key=lambda i: list(RESULT_TONE).index(i["result"])) if current else None
    gate = worst["result"] if worst else None
    answered = gate in ("beyond", "at_limit") or (gate == "within" and "no removal" in action.lower())
    lines = []
    if action.startswith("Confirm the input data first"):
        lines.append("**Confirm the data first** because a serious plausibility issue was found in the inputs. "
                     "Clean data would remove this step.")
    if "do not dispatch" in action.lower():
        slot = rec.get("slot")
        lines.append(f"**Do not dispatch** because the planning bound is {lower:.0f} cycles"
                     + (" (none left)" if lower <= 0 else f", which runs out on {rec.get('deadline')}")
                     + (f" and the first free slot is {slot}" if slot else "")
                     + ". A bound that lasts to the first free slot would let the engine fly until the work.")
    if worst:
        lines.append(_inspection_boundary(worst, case))
    if diverge and answered:
        lines.append(f"**No back-test or extra borescope** because the two methods are {gap:.0f} cycles apart (limit "
                     f"{limit}) and the fresh finding {worst['evidence_id']} is the inspection that disagreement asks "
                     "for.")
    elif at_cap and not diverge:
        lines.append(f"**No action proposed before the next scheduled inspection** because {' and '.join(at_cap)} "
                     "has not left the "
                     f"{th['rul_cap_cycles']:.0f}-cycle cap, so there is no measurable wear yet, and the methods agree "
                     f"within {limit} cycles. Wear below the cap on both methods, or a gap above {limit} cycles, would "
                     "change the plan.")
    elif diverge:
        lines.append(f"**Inspection, not a repair,** because the two methods are {gap:.0f} cycles apart (limit {limit}). "
                     f"If they agreed within {limit} cycles, the planner would pick the {severity} task for this fault.")
    if not (at_cap and not diverge):
        from_rul = "severe" if lower < severe else "moderate" if lower < moderate else "minor"
        bands = {"severe": f"under {severe}", "moderate": f"{severe} to {moderate - 1}", "minor": f"{moderate} or more"}
        text = (f"**Severity {from_rul}** because the planning bound is {lower:.0f} cycles ({bands[from_rul]}). "
                f"Severe is under {severe} cycles, moderate under {moderate}, minor above.")
        if severity != from_rul:
            text += f" The last inspection finding raised it to {severity}."
        lines.append(text)
    world = next((e["content"] for e in evidence if e.get("produced_by") == "maint_planner"
                  and ":world/" in (e.get("source") or "") and "tasks" in (e.get("content") or {})), {})
    swap_ids = {t.get("fmea_id") for t in world.get("tasks") or [] if t.get("task_type") == "engine_change"}
    wait = plan["lead_days"] + plan["slot_days"]
    if gate == "beyond" and swap_ids & set(rec.get("task_ids") or []):
        lines.append(f"**Lease-engine swap** because the finding grounds the engine now and a repair on wing needs "
                     f"{plan['lead_days']} d for parts plus {plan['slot_days']} d to the first free slot ({wait} d).")
    elif not diverge and not (at_cap and not diverge) and "inspection targeting" not in action.lower():
        if swap_ids & set(rec.get("task_ids") or []):
            text = (f"**Lease-engine swap** because the bound covers {plan['rul_days']:.1f} days, but a repair on "
                    f"wing needs {plan['lead_days']} d for parts plus {plan['slot_days']} d to the first free slot "
                    f"({wait} d).")
            short = [p["part_number"] for p in plan.get("short_parts") or []]
            if short and plan["rul_days"] >= plan["slot_days"]:
                text += (f" With {', '.join(short)} on the shelf the wait would be {plan['slot_days']} d, which the "
                         "bound covers, so the repair would be planned on wing.")
            elif wait * cpd <= th["rul_cap_cycles"]:
                text += (f" With a bound of {_days(wait)} ({wait * cpd} cycles) or more, the repair would be planned "
                         "on wing instead.")
            lines.append(text)
        elif swap_ids:
            lines.append(f"**No engine swap** because the bound covers {plan['rul_days']:.1f} days, enough for parts "
                         f"({plan['lead_days']} d) plus the first free slot ({plan['slot_days']} d). Below "
                         f"{_days(wait)} ({wait * cpd} cycles) Cleris would plan a lease-engine swap.")
    for doc in findings.get("documents") or []:
        if doc.get("mandatory") and doc.get("required_task"):
            lines.append(f"**{doc['doc_id']} {doc['section']}** requires {doc['required_task']} within "
                         f"{doc.get('within_cycles')} cycles. No change in the numbers removes it from the plan.")
    return lines


AGENT_LABELS = {"maint_planner": "maintenance planner", "rule_checks": "rule checks", "escalation": "escalation rules"}
STATUS_TONE = {"success": "ok", "needs_revision": "flag", "fallback": "flag", "skipped": "plain", "rejected": "bad",
               "error": "bad"}


def trace_rows(events: list[dict]) -> list[dict[str, Any]]:
    """Trace events as a readable timeline: who acted, who it went to, outcome, model and summary. Zetaris queries and
    context-store calls are marked `call` rows; a model reply replayed from a recording says "recorded"."""
    rows = []
    for n, event in enumerate(events, 1):
        agent = event.get("agent_name") or "?"
        status = event.get("status") or ""
        targets = [t for t in (event.get("target_agent") or "").split(",") if t]
        if status == "needs_revision" and not targets:
            targets = ["maint_planner"]
        model = event.get("model") or ""
        if model and event.get("recorded"):
            model += " (recorded reply)"
        rows.append({
            "step": n,
            "agent": AGENT_LABELS.get(agent, agent),
            "to": ", ".join(AGENT_LABELS.get(t, t) for t in targets),
            "action": (event.get("action") or "").replace("_", " "),
            "status": status.replace("_", " "),
            "tone": STATUS_TONE.get(status, "plain"),
            "model": model,
            "summary": event.get("output_summary") or "",
            "seconds": event.get("duration_s"),
            "call": event.get("action") in CALL_ACTIONS,
        })
    return rows


ACTOR_CHIPS = {"agent": "agent", "gate": "gate", "model": "model", "human": "engineer"}
LOG_ACTORS = {**AGENT_LABELS, "close": "round limit"}
DECISION_TONE = {"accepted": "ok", "approved": "ok", "challenge refuted": "ok", "no escalation": "ok",
                 "no follow-up needed": "ok", "delegated follow-up": "plain", "ran follow-up": "plain",
                 "routed gap": "flag", "ran repair": "plain", "no agent can fix": "bad",
                 "entered inspection finding": "plain", "compared finding with limits": "plain",
                 "recorded fleet decision": "plain",
                 "sent back for revision": "flag", "challenge confirmed": "flag", "escalated": "flag",
                 "asked back": "flag", "rejected": "bad", "failed": "bad", "not accepted at the round limit": "bad",
                 "closed: insufficient evidence": "bad"}


def _utc_time(stamp: str | None, with_date: bool = False) -> str:
    """An ISO time shown in UTC: an offset is converted, a naive legacy value is read as UTC."""
    text = str(stamp or "")
    if "T" not in text:
        return text
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC" if with_date else "%H:%M:%S UTC")


def _signoff_details(entry: dict) -> list[str]:
    """The sign-off's binding: who, when, which brief (and its stored snapshot), the comment, the past-case ratings
    and the versions it ran on. An inspector's entry says when the finding was entered."""
    if entry.get("actor_type") != "human":
        return []
    if not entry.get("signer"):
        verb = "Signed engineering order" if entry.get("decision") == "recorded fleet decision" else "Entered"
        return [f"{verb} {_utc_time(entry.get('timestamp'), with_date=True)}"]
    signer, brief = entry.get("signer") or {}, entry.get("brief") or {}
    who = [signer.get("role"), f"licence {signer['licence_ref']}" if signer.get("licence_ref") else None]
    lines = [f"Signed {_utc_time(entry.get('timestamp'), with_date=True)}" + "".join(f" · {x}" for x in who if x)]
    if brief:
        sha = (brief.get("recommendation_sha256") or "")[:12]
        lines.append(f"Brief {brief.get('case_id')}" + (f" · recommendation fingerprint {sha}" if sha else ""))
    snapshot = entry.get("brief_snapshot") or {}
    if snapshot:
        lines.append(f"Brief as shown stored at {snapshot.get('path')} · SHA-256 {(snapshot.get('sha256') or '')[:12]}")
    if entry.get("wo_id"):
        lines.append(f"Work order {entry['wo_id']}")
    acknowledged = entry.get("acknowledged") or []
    if acknowledged:
        lines.append("Escalations acknowledged: " + "; ".join(
            f"{a.get('severity')} {(a.get('rule') or '').replace('_', ' ')}" for a in acknowledged))
    noted = entry.get("noted") or []
    if noted:
        lines.append("Notes shown and recorded as seen: " + "; ".join(
            (a.get("rule") or "").replace("_", " ") for a in noted))
    reason = next((a["reason"] for a in acknowledged if a.get("reason")), None)
    if reason:
        lines.append(f"Approved despite the urgent escalation because: {reason}")
    if entry.get("comment"):
        lines.append(f"Comment: {entry['comment']}")
    ratings = entry.get("case_feedback") or {}
    if ratings:
        lines.append("Past-case ratings: " + ", ".join(f"{k} {v.replace('_', ' ')}" for k, v in ratings.items()))
    stamp = entry.get("versions") or {}
    if stamp:
        code = str(stamp.get("code") or "unknown")
        lines.append(f"Ran on code {code[:12]}{' (uncommitted changes)' if code.endswith('+dirty') else ''} · asset "
                     f"pack {stamp.get('asset_pack')} · {len(stamp.get('models') or {})} model files hashed · LLM "
                     f"{stamp.get('llm')}")
    return lines


def decision_log_rows(entries: list[dict]) -> list[dict[str, Any]]:
    """Decision-log entries as display rows: actor type chip, who, what, round, time, why, evidence IDs."""
    rows = []
    for n, entry in enumerate(entries, 1):
        kind, actor = entry.get("actor_type") or "agent", entry.get("actor") or "?"
        decision = entry.get("decision") or ""
        inspector = kind == "human" and not entry.get("signer")      # an inspector entered a finding
        chip = ("engineering" if decision == "recorded fleet decision" else "inspector" if inspector
                else ACTOR_CHIPS.get(kind, kind))
        rows.append({
            "step": n, "kind": kind, "chip": chip,
            "actor": LOG_ACTORS.get(actor, actor) if kind in {"agent", "gate"} else actor,
            "decision": decision, "tone": DECISION_TONE.get(decision, "plain"),
            "round": entry.get("round"), "time": _utc_time(entry.get("timestamp")),
            "rationale": entry.get("rationale") or "", "evidence_ids": list(entry.get("evidence_ids") or []),
            "details": _signoff_details(entry), "hash": (entry.get("hash") or "")[:12],
        })
    return rows


def decision_rows(case: dict) -> list[dict]:
    rows = []
    for v in case.get("verdicts") or []:
        model = v.get("model") or "deterministic"
        if v.get("model") and v.get("recorded"):
            model = f"{model} ({v['recorded']})"
        rows.append({"round": v.get("round"), "gate": v.get("gate", "").replace("_", " "),
                     "outcome": v.get("outcome"), "model": model,
                     "gaps": "; ".join(v.get("gaps") or []) or "—"})
    return rows


def trace_chips(trace: list[dict]) -> list[str]:
    chips = []
    for event in trace:
        if event.get("action") in CALL_ACTIONS:         # Zetaris and context-store calls: the Trace tab lists them
            continue
        agent = event.get("agent_name", "?")
        status = event.get("status", "")
        extra = ""
        if agent == "critic":
            extra = f" {status}"
        chips.append(f"{agent}{extra}")
    return chips


def similar_case_summaries(case: dict) -> list[str]:
    """One line per cited past removal: what the shop found and replaced (read from the case-memory record), else
    the record's summary."""
    evidence = case.get("evidence") or {}
    lines = []
    for eid in (case.get("findings") or {}).get("similar_cases") or []:
        item = evidence.get(eid) or {}
        found = (item.get("content") or {}).get("shop_finding")
        lines.append(f"{eid} · {finding_line(found)}" if found else f"{eid}: {item.get('summary') or 'similar case'}")
    return lines


def shop_findings(case: dict) -> list[str]:
    """What the shop found on the cited past removals, each finding once, in sentence case."""
    evidence = case.get("evidence") or {}
    found = [((evidence.get(eid) or {}).get("content") or {}).get("shop_finding")
             for eid in (case.get("findings") or {}).get("similar_cases") or []]
    return list(dict.fromkeys(f["found"] for f in found if f and f.get("found")))


GROUP_TITLES = {"mandatory": "Mandatory now", "recommended": "Recommended, needs engineering approval",
                "reference": "Reference"}
GROUP_NOTES = {
    "mandatory": "The mandate's conditions hold for this engine: compliance is due, and the plan says how.",
    "recommended": "Service bulletin actions. Adopting one needs engineering approval, recorded once per fleet.",
    "reference": "Manuals, procedures and background, plus mandates whose conditions are not met (with why). No "
                 "compliance is due from these.",
}


def document_groups(case: dict) -> list[tuple[str, str, list[dict]]]:
    """(key, title, documents) for the groups that have documents, in the order mandatory, recommended, reference."""
    docs = (case.get("findings") or {}).get("documents") or []
    out = []
    for key, title in GROUP_TITLES.items():
        group = [d for d in docs if (d.get("group") or ("mandatory" if d.get("mandatory") else "reference")) == key]
        if group:
            adopted = key == "recommended" and all(d.get("fleet_decision") == "adopt" for d in group)
            out.append((key, "Recommended, adopted by engineering order" if adopted else title, group))
    return out


def fleet_decision_targets(case: dict) -> list[dict[str, str]]:
    """The bulletins an sb_adoption note asks engineering to decide on, with the fleet the decision would cover."""
    fleet = (case.get("asset") or {}).get("variant")
    by_id = {d["evidence_id"]: d for d in (case.get("findings") or {}).get("documents") or []}
    out = []
    for esc in case.get("escalations") or []:
        if esc.get("rule") != "sb_adoption" or not fleet:
            continue
        doc = next((by_id[i] for i in esc.get("evidence_ids") or [] if i in by_id
                    and by_id[i].get("doc_type") == "service_bulletin"), None)
        if doc and all(t["doc_id"] != doc["doc_id"] for t in out):
            out.append({"doc_id": doc["doc_id"], "section": doc["section"], "fleet": fleet})
    return out


_ZETARIS_DBS = frozenset({"telemetry_pg", "cmms_pg", "knowledge_pg", "s3_files"})


def zetaris_sql(item: dict) -> dict | None:
    """Pretty-printed SQL, the source tables the statement reads, and the query time, for a record whose source
    starts with ``zetaris:``. A snapshot read and every other record return None."""
    source = item.get("source")
    content = item.get("content")
    if not isinstance(content, dict):
        return None
    query = content.get("query")
    if (not isinstance(source, str) or not source.startswith("zetaris:")
            or not isinstance(query, str) or not query.strip()):
        return None
    try:
        statements = [s for s in sqlglot.parse(query, read="spark") if s is not None]
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return None
    statement = statements[0]
    tables: list[str] = []
    for table in statement.find_all(exp.Table):
        if table.db in _ZETARIS_DBS and table.name:
            qualified = f"{table.db}.{table.name}"
            if qualified not in tables:
                tables.append(qualified)
    return {"sql": statement.sql(dialect="spark", pretty=True), "tables": tables,
            "duration_s": content.get("duration_s")}


def load_trace_file(path) -> list[dict[str, Any]]:
    from pathlib import Path
    import json
    file = Path(path)
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
