"""Explains the two remaining-life methods. Facts (sensors, accuracy, thresholds) are read from the trained
model artifacts, so the explanation cannot drift from the models the agents actually run."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from models.rul import BASELINE_CYCLES, MIN_HISTORY, SMOOTH_WINDOW, _exp_trend
from tools.model_tools import load_models

LAYOUT = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#e8eef5"),
              margin=dict(l=40, r=16, t=30, b=40))


def _names(sensors, pack: dict) -> list[str]:
    return [pack["sensors"][s]["name"] for s in sensors]


def method_facts(pack: dict) -> dict[str, Any]:
    models = load_models(pack)
    report = models.report
    a, b = models.rul_a, models.rul_b
    fleet = report["active_fleet"]
    return {
        "train_engines": report["train_engines"],
        "holdout_engines": report["holdout_engines"],
        "cap": a.cap,
        "a": {
            "sensors": _names(a.sensors, pack),
            "features": len(a.features),
            "window": max(int(f.rsplit("_mean", 1)[1]) for f in a.features if "_mean" in f),
            **{k: fleet["rul_a"][k] for k in ("engines", "rmse", "mae", "interval_coverage", "mean_interval_width")},
        },
        "b": {
            "sensors": _names(b.weights, pack),
            "baseline_cycles": BASELINE_CYCLES,
            "min_history": MIN_HISTORY,
            "smooth": SMOOTH_WINDOW,
            "healthy_band": round(b.onset, 2),
            "thresholds": {k: round(v, 2) for k, v in b.thresholds.items()},
            **{k: fleet["rul_b"][k] for k in ("engines", "rmse", "mae", "not_fitted")},
        },
        "divergence_share": fleet["a_b_divergence_share"],
    }


def health_index_figure(cycles: pd.DataFrame, pack: dict) -> tuple[go.Figure | None, str | None]:
    """Method B's working: the engine's health index per cycle, the fitted exponential trend projected forward,
    the healthy band and the failure-threshold band learned from past engines."""
    model = load_models(pack).rul_b
    if cycles.empty:
        return None, None
    deg = model.degradation(cycles)
    cycle = deg["cycle"].to_numpy(float)
    raw, smoothed = deg["degradation"].to_numpy(), deg["smoothed"].to_numpy()
    th = model.thresholds
    fig = go.Figure()
    fig.add_hrect(y0=min(raw.min(), 0) - 0.05, y1=model.onset, fillcolor="#22c55e", opacity=0.08, line_width=0,
                  annotation_text="healthy band", annotation_position="bottom left")
    fig.add_hrect(y0=th["p10"], y1=th["p90"], fillcolor="#e11d48", opacity=0.12, line_width=0,
                  annotation_text="failure level (past engines, 10th–90th)", annotation_position="top left")
    fig.add_hline(y=th["p50"], line_dash="dot", line_color="#e11d48")
    fig.add_trace(go.Scatter(x=cycle, y=raw, mode="markers", name="health index per flight",
                             marker=dict(size=3, color="#64748b")))
    fig.add_trace(go.Scatter(x=cycle, y=smoothed, mode="lines", name=f"{SMOOTH_WINDOW}-flight average",
                             line=dict(color="#93c5fd")))
    note = None
    fitted = model.fit_trend(cycle, raw) if len(cycle) >= MIN_HISTORY and smoothed[-1] >= model.onset else None
    if fitted:
        a, b = fitted
        last = cycle[-1]
        horizon = last + model.cap
        crossing = np.log((th["p90"] - model.baseline) / a + 1.0) / b
        end = min(max(crossing, last) + 5, horizon)
        t = np.linspace(cycle[0], end, 200)
        fig.add_trace(go.Scatter(x=t, y=_exp_trend(t, model.baseline, a, b), mode="lines", name="fitted trend",
                                 line=dict(color="#f59e0b", dash="dash")))
    else:
        note = "No trend drawn: the health index is still in the healthy band (or history is too short)."
    fig.add_vline(x=cycle[-1], line_color="#e8eef5", line_dash="dot", annotation_text="now")
    fig.update_layout(height=340, xaxis_title="flight (cycle)", yaxis_title="health index (0 = new)",
                      legend=dict(orientation="h", y=1.18), **LAYOUT)
    fig.update_xaxes(gridcolor="#1e293b")
    fig.update_yaxes(gridcolor="#1e293b")
    return fig, note


def drivers_figure(drivers: list[dict], pack: dict) -> go.Figure | None:
    """Method A's working: how much each sensor moved the estimate, in flights (negative = fewer flights left)."""
    if not drivers:
        return None
    rows = sorted(drivers, key=lambda d: d["contribution"])
    labels = [f"{pack['sensors'][d['sensor']]['name']} ({d['component']})" for d in rows]
    values = [d["contribution"] for d in rows]
    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h",
                           marker_color=["#e11d48" if v < 0 else "#22c55e" for v in values],
                           text=[f"{v:+.1f}" for v in values], textposition="outside"))
    fig.update_layout(height=60 + 34 * len(rows), xaxis_title="effect on flights left", **LAYOUT)
    fig.update_xaxes(gridcolor="#1e293b", zeroline=True, zerolinecolor="#64748b")
    return fig
