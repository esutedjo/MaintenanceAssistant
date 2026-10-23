"""Engine schematic with the suspected module highlighted, and the illustrative photos listed in the asset pack."""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#e8eef5"),
              margin=dict(l=10, r=10, t=10, b=10))
IDLE = "rgba(100,116,139,0.35)"


def component_probabilities(findings: dict, pack: dict) -> dict[str, float]:
    probs: dict[str, float] = {}
    for m in findings.get("fault_modes") or []:
        comp = (pack.get("failure_modes", {}).get(m["failure_mode"]) or {}).get("component")
        if comp:
            probs[comp] = max(probs.get(comp, 0.0), m.get("probability") or 0.0)
    return probs


def _fill(prob: float | None, worn: bool) -> str:
    if not prob:
        return IDLE
    alpha = 0.25 + 0.6 * prob
    return f"rgba(225,29,72,{alpha:.2f})" if worn else f"rgba(245,158,11,{alpha * 0.6:.2f})"


def engine_figure(pack: dict, probs: dict[str, float], drivers: list[str] = (), signature: list[str] = (),
                  worn: bool = True) -> go.Figure | None:
    """Side view of the engine. Modules carrying a fault mode are shaded by its probability (red when the engine
    shows wear, amber when it is still healthy); sensors are pinned above the module they measure."""
    modules = pack.get("diagram") or []
    if not modules:
        return None
    fig = go.Figure()
    for m in modules:
        (x0, x1), (i0, i1), (o0, o1) = m["x"], m["inner"], m["outer"]
        prob = probs.get(m["component"])
        name = (pack.get("components") or {}).get(m["component"], m["label"])
        hover = f"{m['label']} · {name}" + (f"<br>fault-mode probability {prob:.2f}" if prob else "")
        for sign in (1, -1):
            fig.add_trace(go.Scatter(
                x=[x0, x1, x1, x0, x0], y=[sign * i0, sign * i1, sign * o1, sign * o0, sign * i0],
                fill="toself", fillcolor=_fill(prob, worn), mode="lines",
                line=dict(color=("#e11d48" if worn else "#f59e0b") if prob else "#475569", width=2 if prob else 1),
                hoveron="fills", hoverinfo="text", text=hover, showlegend=False))
        fig.add_annotation(x=(x0 + x1) / 2, y=-(i0 + o0 + i1 + o1) / 4, text=m["label"], showarrow=False,
                           font=dict(size=10, color="#f8fafc" if prob else "#cbd5e1"))
    span = max(m["x"][1] for m in modules)
    fig.add_shape(type="line", x0=0, x1=span, y0=0, y1=0, line=dict(color="#94a3b8", dash="dashdot", width=1))
    fig.add_annotation(x=0, y=0, ax=-40, ay=0, text="air", showarrow=True, arrowhead=2, arrowcolor="#94a3b8",
                       font=dict(color="#94a3b8"), xanchor="right")
    _sensor_pins(fig, pack, modules, set(drivers), set(signature))
    top = max(max(m["outer"]) for m in modules)
    fig.update_layout(height=360, showlegend=False, **LAYOUT)
    fig.update_xaxes(visible=False, range=[-0.6, span + 0.3])
    fig.update_yaxes(visible=False, range=[-top - 0.3, top + 2.6], scaleanchor="x", scaleratio=0.6)
    return fig


def _sensor_pins(fig: go.Figure, pack: dict, modules: list[dict], drivers: set[str], signature: set[str]) -> None:
    centre = {}
    for m in modules:
        centre.setdefault(m["component"], (sum(m["x"]) / 2, max(m["outer"])))
    stack: dict[str, int] = {}
    xs, ys, labels, colours, hovers = [], [], [], [], []
    for sensor, spec in (pack.get("sensors") or {}).items():
        comp = spec.get("component")
        if comp not in centre or all(d == "none" for d in (spec.get("direction") or {}).values()):
            continue
        k = stack.get(comp, 0)
        stack[comp] = k + 1
        x, top = centre[comp]
        xs.append(x)
        ys.append(max(top, 3.0) + 0.35 + 0.38 * k)
        labels.append(spec["name"])
        role = "SHAP driver" if sensor in drivers else "signature sensor" if sensor in signature else "monitored"
        colours.append("#f59e0b" if sensor in drivers else "#22d3ee" if sensor in signature else "#94a3b8")
        hovers.append(f"{spec['name']} ({sensor}): {spec.get('description', '')}<br>{role}")
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers+text", text=labels, textposition="middle right",
                             marker=dict(size=8, color=colours), textfont=dict(size=10, color=colours),
                             hoverinfo="text", hovertext=hovers, showlegend=False))


def photos(pack: dict, component: str | None = None) -> list[dict]:
    """Photo entries for a component (or all), with an absolute `path`. Missing files are skipped."""
    spec = pack.get("images") or {}
    folder = ROOT / spec.get("directory", "")
    out = []
    for item in spec.get("items") or []:
        path = folder / item["file"]
        if (component is None or item["component"] == component) and path.exists():
            out.append({**item, "path": str(path)})
    return out


def credit(item: dict) -> str:
    licence = (f"[{item['license']}]({item['license_url']})" if item.get("license_url") else item["license"])
    return f"{item['caption']}. Photo: {item['author']}, {licence}, [Wikimedia Commons]({item['source']})."
