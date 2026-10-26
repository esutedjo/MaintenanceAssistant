"""Engine schematic with the suspected module highlighted, and the illustrative photos listed in the asset pack."""

from __future__ import annotations

import html
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


MODE_COLOURS = ["#fb7185", "#f59e0b", "#a78bfa", "#34d399"]


def moving_sensors(pack: dict) -> dict[str, list[tuple[str, bool]]]:
    """Per component, the sensors that move with wear as (name, tells the fault modes apart)."""
    out: dict[str, list[tuple[str, bool]]] = {}
    for spec in (pack.get("sensors") or {}).values():
        directions = set((spec.get("direction") or {}).values())
        if directions and directions != {"none"}:
            out.setdefault(spec.get("component"), []).append((spec["name"], len(directions) > 1))
    return out


def engine_svg(pack: dict) -> str | None:
    """Labelled side view for the help panel, drawn from the pack's diagram: each module, the sensors that move
    with wear above the module they measure, and each failure mode marked on its component with the fleets that
    show it. A static SVG, so it scales on a tablet and needs no hover."""
    modules = pack.get("diagram") or []
    if not modules:
        return None
    esc = html.escape
    sx, rx, left, axis = 105, 34, 40, 226              # px per axial unit, px per radius unit, margin, centre line

    def px(x: float) -> float:
        return left + x * sx

    modes = pack.get("failure_modes") or {}
    colour = {m: MODE_COLOURS[i % len(MODE_COLOURS)] for i, m in enumerate(modes)}
    by_component = {spec.get("component"): m for m, spec in modes.items()}
    sensors_on = moving_sensors(pack)
    anchors = {}
    for m in modules:
        anchors.setdefault(m["component"], ((m["x"][0] + m["x"][1]) / 2, max(m["outer"])))
    leaders = [px(x) for c, (x, _) in anchors.items() if c in sensors_on]
    parts = []
    for m in modules:
        (x0, x1), (i0, i1), (o0, o1) = m["x"], m["inner"], m["outer"]
        mode = by_component.get(m["component"])
        fill = f'fill="{colour[mode]}" fill-opacity="0.32" stroke="{colour[mode]}" stroke-width="2"' if mode else \
            'fill="#64748b" fill-opacity="0.28" stroke="#64748b" stroke-width="1"'
        for sign in (-1, 1):
            pts = [(x0, i0), (x1, i1), (x1, o1), (x0, o0)]
            parts.append('<polygon points="' + " ".join(f"{px(x):.0f},{axis + sign * r * rx:.0f}" for x, r in pts)
                         + f'" {fill}/>')
        size = 10 if m["label"] == "Combustor" else 12
        mid = axis - (i0 + i1 + o0 + o1) / 4 * rx
        # A wide module (the bypass duct) has other modules' sensor leader lines running through it: move its
        # label along the span until no leader crosses it.
        half = len(m["label"]) * size * 0.3 + 6
        own = px((x0 + x1) / 2)
        spots = [px(x0 + f * (x1 - x0)) for f in (0.5, 0.3, 0.7, 0.2, 0.8)]
        x = next((s for s in spots if all(abs(s - lx) > half for lx in leaders if lx != own)), spots[0])
        parts.append(f'<text x="{x:.0f}" y="{mid + 4:.0f}" font-size="{size}" text-anchor="middle" '
                     f'fill="#f8fafc">{esc(m["label"])}</text>')
    span = max(m["x"][1] for m in modules)
    top = max(max(m["outer"]) for m in modules)
    parts.append(f'<line x1="{px(0):.0f}" y1="{axis}" x2="{px(span):.0f}" y2="{axis}" stroke="#94a3b8" '
                 'stroke-dasharray="6 4"/>')
    parts.append(f'<text x="{left - 4}" y="{axis + 4}" font-size="11" text-anchor="end" fill="#94a3b8">air</text>')
    # Sensors stacked above their module, with a leader line down to it.
    for component, sensors in sensors_on.items():
        if component not in anchors:
            continue
        x, outer = anchors[component]
        base = axis - top * rx - 14
        for k, (name, signature) in enumerate(sensors):
            parts.append(f'<text x="{px(x):.0f}" y="{base - 14 * k:.0f}" font-size="11" text-anchor="middle" '
                         f'fill="{"#22d3ee" if signature else "#cbd5e1"}">{esc(name)}</text>')
        parts.append(f'<line x1="{px(x):.0f}" y1="{base + 4:.0f}" x2="{px(x):.0f}" y2="{axis - outer * rx:.0f}" '
                     'stroke="#94a3b8" stroke-width="1" stroke-dasharray="2 3"/>')
    # Failure modes: a callout under the component, naming the fleets that can show it.
    variants = pack.get("variants") or {}
    for mode, spec in modes.items():
        if spec.get("component") not in anchors:
            continue
        x, _ = anchors[spec["component"]]
        fleets = " and ".join(v for v, listed in variants.items() if mode in listed)
        y = axis + top * rx + 26
        parts.append(f'<line x1="{px(x):.0f}" y1="{axis + rx:.0f}" x2="{px(x):.0f}" y2="{y - 12}" '
                     f'stroke="{colour[mode]}" stroke-width="1.5"/>')
        parts.append(f'<text x="{px(x):.0f}" y="{y}" font-size="12" font-weight="bold" text-anchor="middle" '
                     f'fill="{colour[mode]}">{esc(spec.get("label", mode))}</text>')
        if fleets:
            parts.append(f'<text x="{px(x):.0f}" y="{y + 15}" font-size="11" text-anchor="middle" '
                         f'fill="#cbd5e1">{esc(fleets)}</text>')
    legend = axis + top * rx + 72
    parts.append(f'<text x="{left}" y="{legend}" font-size="11" fill="#cbd5e1">Shaded: the part each fault mode '
                 'wears. Sensor names sit above the part they measure. Not to scale.</text>')
    parts.append(f'<text x="{left}" y="{legend + 16}" font-size="11" fill="#cbd5e1"><tspan fill="#22d3ee">Cyan'
                 '</tspan>: moves one way for one fault and the other way for the other, so it tells them apart. '
                 'Grey: rises or falls with any wear.</text>')
    width, height = int(px(span) + 40), int(legend + 28)
    title = "Turbofan side view: " + ", ".join(dict.fromkeys(m["label"] for m in modules))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
            f'font-family="Helvetica, Arial, sans-serif"><title>{esc(title)}</title>' + "".join(parts) + "</svg>")


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
