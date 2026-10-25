"""Pure helpers for the decision brief. Streamlit rendering lives in ui/app.py so these stay testable."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

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
.esc-title { font-weight: 700; font-size: .95rem; }
.esc-sev { font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; margin-right: .5rem; }
.esc.urgent .esc-sev { color: #fda4af; }
.esc.warning .esc-sev { color: #fcd34d; }
.esc-msg { color: #e2e8f0; font-size: .92rem; margin-top: .15rem; }
.esc-act { color: #cbd5e1; font-size: .85rem; margin-top: .3rem; }
.plan-card { background: #16202e; border: 1px solid #2b3a4f; border-radius: 8px; padding: .8rem 1rem;
             margin: .4rem 0 1.2rem 0; }
.plan-label { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: #93c5fd; }
.plan-action { font-size: 1.1rem; font-weight: 600; margin: .15rem 0 .5rem 0; }
.plan-facts { display: flex; flex-wrap: wrap; gap: .3rem 1.6rem; font-size: .9rem; color: #cbd5e1; }
.plan-facts b { color: #e8eef5; }
.plan-graph { margin-top: .55rem; font-size: .88rem; color: #cbd5e1; }
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
.plain { background: #16202e; border-left: 4px solid #60a5fa; padding: .75rem 1rem; border-radius: 6px;
         margin: 0 0 1rem 0; line-height: 1.6; font-size: 1.02rem; }
.plain-title { font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: #93c5fd;
               margin-bottom: .25rem; }
.explain { color: #94a3b8; font-size: .88rem; border-left: 2px solid #334155; padding: .1rem 0 .1rem .6rem;
           margin: -.2rem 0 .8rem 0; }
</style>
"""


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
    "rul_below_lead_time": "The engine wears out before a normal repair can happen",
    "rul_divergence": "The two remaining-life methods disagree",
    "ambiguous_fault": "Two fault modes are too close to call",
    "data_quality": "Input data failed a plausibility check",
    "knowledge_drift": "The failure-mode table lists work the World Model does not",
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
    return {"severity": esc.get("severity") or "warning", "title": ESCALATION_TITLES.get(rule, rule.replace("_", " ")),
            "message": message, "proposed": proposed, "notes": notes}


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
        chips.append((f"LLM critic: {', '.join(models)}" if models else "critic: deterministic checks only", "plain"))
    escalations = case.get("escalations") or []
    if escalations:
        chips.append((f"{len(escalations)} escalation{'s' if len(escalations) > 1 else ''}", "flag"))
    chips.append((f"{len(case.get('evidence') or {})} evidence records", "plain"))
    return chips


AGENT_LABELS = {"maint_planner": "maintenance planner", "rule_checks": "rule checks", "escalation": "escalation rules"}
STATUS_TONE = {"success": "ok", "needs_revision": "flag", "fallback": "flag", "skipped": "plain", "rejected": "bad",
               "error": "bad"}


def trace_rows(events: list[dict]) -> list[dict[str, Any]]:
    """Trace events as a readable timeline: who acted, who it went to, outcome, model and summary."""
    rows = []
    for n, event in enumerate(events, 1):
        agent = event.get("agent_name") or "?"
        status = event.get("status") or ""
        targets = [t for t in (event.get("target_agent") or "").split(",") if t]
        if status == "needs_revision" and not targets:
            targets = ["maint_planner"]
        rows.append({
            "step": n,
            "agent": AGENT_LABELS.get(agent, agent),
            "to": ", ".join(AGENT_LABELS.get(t, t) for t in targets),
            "action": (event.get("action") or "").replace("_", " "),
            "status": status.replace("_", " "),
            "tone": STATUS_TONE.get(status, "plain"),
            "model": event.get("model") or "",
            "summary": event.get("output_summary") or "",
            "seconds": event.get("duration_s"),
        })
    return rows


def decision_rows(case: dict) -> list[dict]:
    rows = []
    for v in case.get("verdicts") or []:
        rows.append({"round": v.get("round"), "gate": v.get("gate", "").replace("_", " "),
                     "outcome": v.get("outcome"), "model": v.get("model") or "deterministic",
                     "gaps": "; ".join(v.get("gaps") or []) or "—"})
    return rows


def trace_chips(trace: list[dict]) -> list[str]:
    chips = []
    for event in trace:
        agent = event.get("agent_name", "?")
        status = event.get("status", "")
        extra = ""
        if agent == "critic":
            extra = f" {status}"
        chips.append(f"{agent}{extra}")
    return chips


def similar_case_summaries(case: dict) -> list[str]:
    evidence = case.get("evidence") or {}
    lines = []
    for eid in (case.get("findings") or {}).get("similar_cases") or []:
        item = evidence.get(eid) or {}
        lines.append(f"{eid}: {item.get('summary') or 'similar case'}")
    return lines


def load_trace_file(path) -> list[dict[str, Any]]:
    from pathlib import Path
    import json
    file = Path(path)
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
