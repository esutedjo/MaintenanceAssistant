"""Pure helpers for the decision brief. Streamlit rendering lives in ui/app.py so these stay testable."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

CSS = """
<style>
.block-container { padding-top: 1.1rem; max-width: 1180px; }
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
