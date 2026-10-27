"""A replay of live runs that needs nothing installed: one self-contained HTML page (docs/index.html) built from
committed run files, for GitHub Pages or for opening straight from a clone. Run: uv run task replay

  uv run task replay                              # rebuild docs/index.html from docs/replay/data/ (no network)
  uv run python -m tools.replay --record          # live mode only: record the runs not yet recorded, then rebuild
  uv run python -m tools.replay --record --all    # record all six again

--record sends each file in input_examples/ through the same path as `run.py --input` (read-only: inspection
findings apply to that run and are not stored), keeps each run's JSON result and its trace, and reads GET /health in
the same process, so the page shows which services answered. A run is kept only if it completed with no degraded
component (Zetaris stalls now and then: it is taken again), and no file is written that contains a setting's value,
a URL or an AWS host (tools/live_evidence.py `leaks`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from settings import ROOT, Settings, get_settings

DATA = ROOT / "docs" / "replay" / "data"
PAGE = ROOT / "docs" / "index.html"
REPO = "https://github.com/esutedjo/MaintenanceAssistant"
VIDEO = "https://youtu.be/rbfwq12gzoI"
# (key, input file, label): the order the page lists them.
CASES = [
    ("hpc", "critical_hpc.json", "Compressor wear with an airworthiness directive due"),
    ("fan", "fan_degradation.json", "Fan wear in the second fleet"),
    ("disagree", "methods_disagree.json", "The two remaining-life methods disagree"),
    ("inspection-beyond", "inspection_beyond_limits.json", "A borescope finding beyond the manual's limit"),
    ("inspection-within", "inspection_within_limits.json", "A borescope finding within limits"),
    ("healthy", "healthy_engine.json", "A healthy engine"),
]
RETRIES = 3         # Zetaris stalls now and then; a degraded run is taken again


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(files: dict[str, str], settings: Settings, data: Path) -> bool:
    from tools.live_evidence import leaks

    leaked = sorted({name for text in files.values() for name in leaks(text, settings)})
    if leaked:
        print(f"  not written: the files would contain {', '.join(leaked)}")
        return False
    data.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (data / name).write_text(text, encoding="utf-8")
    return True


def record(settings: Settings, data: Path = DATA, again: bool = False) -> int:
    """Run the example inputs live (those not yet in `data`, or all with `again`), keep each run that completed with
    nothing degraded, then read /health. Exit 1 if a run could not be recorded."""
    if settings.sample_mode or settings.zetaris_mode != "live":
        print("--record needs live mode (SAMPLE_MODE off, ZETARIS_MODE=live).")
        return 2
    import run

    failed = []
    for key, name, _ in CASES:
        if not again and (data / f"{key}.result.json").exists():
            continue
        payload = json.loads((ROOT / "input_examples" / name).read_text(encoding="utf-8"))
        for attempt in range(1 + RETRIES):
            code, result = run.handle(payload, settings, local=True)
            if code == 200 and not result.get("degraded"):
                break
            print(f"  {key}: HTTP {code}, degraded {result.get('degraded')}; "
                  + ("again" if attempt < RETRIES else "giving up"), flush=True)
        else:
            failed.append(key)
            continue
        files = {f"{key}.result.json": json.dumps(result, indent=1, ensure_ascii=False, default=str) + "\n",
                 f"{key}.trace.jsonl": (ROOT / result["trace_file"]).read_text(encoding="utf-8")}
        if not _write(files, settings, data):
            failed.append(key)
            continue
        print(f"  {key}: {result['status']} in {result['duration_s']} s, run {result['run_id']}", flush=True)
    health = run.health_payload(settings)
    for _ in range(3):      # the first read in a process can still be checking
        if health.get("checked_at"):
            break
        time.sleep(2)
        health = run.health_payload(settings)
    _write({"health.json": json.dumps({"captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                       "health": health}, indent=1, ensure_ascii=False) + "\n"}, settings, data)
    if failed:
        print(f"Not recorded: {', '.join(failed)}. Run --record again; recorded runs are kept.")
    return 1 if failed else 0


def load_runs(data: Path = DATA) -> dict:
    runs = []
    for key, name, label in CASES:
        result_path = data / f"{key}.result.json"
        if not result_path.exists():
            continue
        runs.append({"key": key, "label": label, "input": f"input_examples/{name}",
                     "result": json.loads(result_path.read_text(encoding="utf-8")),
                     "trace": _read_jsonl(data / f"{key}.trace.jsonl")})
    health_path = data / "health.json"
    health = json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else None
    return {"runs": runs, "health": health, "repo": REPO, "video": VIDEO}


def static_summary(bundle: dict) -> str:
    """The runs as plain HTML, for readers without JavaScript and tools that read the page as text. The script
    hides it once the interactive page has rendered."""
    from tools.live_evidence import CHALLENGES

    rows, totals, sources = [], {"zet": 0, "wm": 0, "hm": 0, "nv": 0, "raised": 0, "confirmed": 0}, set()
    for run in bundle["runs"]:
        result, trace = run["result"], run["trace"]
        reads = [line for line in trace if line["action"] == "zetaris_query"]
        for line in reads:
            parts = line["output_summary"].split(" · ")
            sources |= {table.split(".")[0] for table in (parts[1] if len(parts) > 1 else "").split(" + ") if table}
        calls = [line for line in trace if line["action"] == "context_call"]
        hmem = sum(1 for line in calls if line["input_summary"].startswith("H-MEM"))
        live = [call for call in result.get("model_calls") or [] if not call.get("recorded")]
        found = [m for line in trace if line.get("model") and (m := CHALLENGES.search(line["output_summary"]))]
        raised, confirmed = sum(int(m.group(1)) for m in found), sum(int(m.group(2)) for m in found)
        for key, value in (("zet", len(reads)), ("wm", len(calls) - hmem), ("hm", hmem), ("nv", len(live)),
                           ("raised", raised), ("confirmed", confirmed)):
            totals[key] += value
        plan = result.get("recommendation") or {}
        model = ", ".join(sorted({call["model"].split("/")[-1] for call in live})) or "none"
        rows.append(f"<tr><td>{escape((result.get('asset') or {}).get('asset_id', ''))}</td><td>{escape(run['label'])}"
                    f"</td><td>{escape((plan.get('action') or '').split(';')[0])}</td>"
                    f"<td>{escape(str(plan.get('deadline') or '-'))}</td><td>{len(reads)}</td><td>{len(calls) - hmem}</td>"
                    f"<td>{hmem}</td><td>{escape(model)}: {raised} challenges, {confirmed} confirmed by code</td></tr>")
    when = ((bundle.get("health") or {}).get("captured_at") or "")[:16].replace("T", " ")
    return (f"<h2>Live runs in this replay</h2><p>{len(bundle['runs'])} live runs, recorded up to {escape(when)} UTC, "
            f"against Zetaris ({escape(', '.join(sorted(sources)))}: three AWS RDS Postgres databases and an S3 file), "
            "the Meterless context service (World Model and H-MEM) and Meterless Nightshift serving NVIDIA Nemotron. "
            f"Together: {totals['zet']} live Zetaris SQL reads, {totals['wm']} World Model queries, {totals['hm']} "
            f"H-MEM searches and {totals['nv']} live Nemotron replies with {totals['raised']} checkable challenges, "
            f"{totals['confirmed']} of them confirmed by code. With JavaScript this page shows each brief, the evidence "
            "behind every claim, the gates, every call and the decision log. Raw run files: "
            f'<a href="{REPO}/tree/main/docs/replay/data">docs/replay/data</a>.</p>'
            '<div class="scroll"><table><tr><th>Engine</th><th>Case</th><th>Decision</th><th>Deadline</th>'
            "<th>Zetaris reads</th><th>World Model</th><th>H-MEM</th><th>Nemotron via Nightshift</th></tr>"
            + "".join(rows) + "</table></div>")


def build(data: Path = DATA, page: Path = PAGE) -> Path:
    bundle = load_runs(data)
    if not bundle["runs"]:
        raise SystemExit(f"No recorded runs in {data}; record them first (--record, live mode).")
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(TEMPLATE.replace("/*STATIC*/", static_summary(bundle)).replace("/*DATA*/", payload),
                    encoding="utf-8")
    (page.parent / ".nojekyll").touch()          # serve docs/ as plain files on GitHub Pages
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the no-install replay page from committed live runs.")
    parser.add_argument("--record", action="store_true", help="live mode only: record the runs not yet recorded")
    parser.add_argument("--all", action="store_true", help="with --record: record all six again")
    args = parser.parse_args(argv)
    if args.record and (code := record(get_settings(), again=args.all)):
        return code
    page = build()
    print(f"Wrote {page.relative_to(ROOT).as_posix()} ({page.stat().st_size // 1024} KB)")
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cleris replay</title>
<meta name="description" content="Live runs of Cleris, the MRO clearance assistant: Zetaris, Meterless World Model, H-MEM and Nightshift, and NVIDIA Nemotron, replayed with the evidence, gates and decision log.">
<style>
:root {
  --bg: #f6f7f9; --card: #ffffff; --fg: #17202b; --muted: #5b6676; --border: #dde2e8; --accent: #1f5fae;
  --soft: #eef2f7; --ok: #1d7a46; --warn: #9a5b00; --warn-soft: #fdf1de; --bad: #b3261e; --bad-soft: #fbe9e7;
  --zet: #1f5fae; --zet-soft: #e8f0fb; --wm: #0f7c80; --wm-soft: #e1f3f3; --hm: #2f7d32; --hm-soft: #e6f3e6;
  --ns: #6b3fa0; --ns-soft: #f0e9f8; --nv: #4d7a00; --nv-soft: #eef6dc; --code: #f0f2f5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #11151b; --card: #182029; --fg: #e6ebf1; --muted: #9aa6b5; --border: #2a3542; --accent: #7fb0ef;
    --soft: #1e2833; --ok: #6cc596; --warn: #f0b45a; --warn-soft: #3a2a12; --bad: #f08a80; --bad-soft: #3d1d1a;
    --zet: #7fb0ef; --zet-soft: #1c2c40; --wm: #5cc8cc; --wm-soft: #15302f; --hm: #7fcf84; --hm-soft: #1a301b;
    --ns: #c3a2ee; --ns-soft: #2b2140; --nv: #a6d84a; --nv-soft: #26310f; --code: #202a35;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); }
.wrap { max-width: 1160px; margin: 0 auto; padding: 24px 16px 64px; }
header h1 { margin: 0 0 4px; font-size: 28px; letter-spacing: -0.01em; }
header .sub { color: var(--muted); margin: 0 0 12px; max-width: 860px; }
.links { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 14px; margin-bottom: 14px; }
.strip { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.chip { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border); background: var(--card); border-radius: 999px; padding: 2px 10px; font-size: 13px; }
.chip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex: none; }
.chip.ok .dot { background: var(--ok); } .chip.warn .dot { background: var(--warn); }
.note { color: var(--muted); font-size: 13px; }
h2.band { font-size: 17px; margin: 22px 0 8px; }
.sponsors { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1000px) { .sponsors { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 520px) { .sponsors { grid-template-columns: 1fr; } }
.sp { background: var(--card); border: 1px solid var(--border); border-top: 4px solid var(--c); border-radius: 12px; padding: 10px 12px; }
.sp .who { font-size: 12px; font-weight: 700; color: var(--c); text-transform: uppercase; letter-spacing: .04em; }
.sp .n { font-size: 26px; font-weight: 700; line-height: 1.2; }
.sp .what { font-size: 13px; color: var(--muted); }
.c-zet { --c: var(--zet); --cs: var(--zet-soft); } .c-wm { --c: var(--wm); --cs: var(--wm-soft); }
.c-hm { --c: var(--hm); --cs: var(--hm-soft); } .c-ns { --c: var(--ns); --cs: var(--ns-soft); } .c-nv { --c: var(--nv); --cs: var(--nv-soft); }
.c-oth { --c: var(--muted); --cs: var(--soft); }
nav.cases { display: grid; grid-template-columns: repeat(auto-fill, minmax(245px, 1fr)); gap: 10px; margin: 8px 0 16px; }
nav.cases button { text-align: left; border: 1px solid var(--border); background: var(--card); color: var(--fg); border-radius: 10px; padding: 10px 12px; cursor: pointer; font: inherit; }
nav.cases button[aria-current="true"] { border-color: var(--accent); box-shadow: 0 0 0 2px var(--soft); }
nav.cases .eng { font-weight: 600; } nav.cases .lab { font-size: 13px; color: var(--muted); } nav.cases .act { font-size: 13px; margin-top: 4px; }
section.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; margin: 12px 0; }
section.card h2 { font-size: 16px; margin: 0 0 10px; }
section.card h3 { font-size: 14px; margin: 14px 0 6px; }
.q { font-size: 18px; margin: 0 0 6px; }
.verdict { font-size: 20px; font-weight: 600; margin: 0 0 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 10px; }
.kv { border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; }
.kv .k { font-size: 12px; color: var(--muted); } .kv .v { font-size: 17px; font-weight: 600; } .kv .s { font-size: 12px; color: var(--muted); }
.panel { border: 1px solid var(--border); border-left: 4px solid var(--c); border-radius: 10px; padding: 10px 12px; margin: 10px 0; background: linear-gradient(90deg, var(--cs), transparent 40%); }
.panel .ph { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
.panel .ph strong { color: var(--c); }
.esc { border-left: 4px solid var(--warn); background: var(--warn-soft); border-radius: 8px; padding: 8px 12px; margin: 8px 0; }
.esc.urgent { border-color: var(--bad); background: var(--bad-soft); }
.esc .rule { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-size: 12px; color: var(--muted); font-weight: 600; }
.scroll { overflow-x: auto; }
.claim { padding: 6px 0; border-bottom: 1px solid var(--border); }
.claim:last-child { border-bottom: 0; }
.ev { font: 12px ui-monospace, SFMono-Regular, Consolas, monospace; border: 1px solid var(--border); background: var(--cs, var(--soft)); color: var(--c, var(--accent)); border-radius: 6px; padding: 0 6px; margin: 1px 4px 1px 0; cursor: pointer; }
.evbox { margin: 6px 0 2px; padding: 8px 10px; background: var(--code); border-radius: 8px; font-size: 13px; }
.badge { display: inline-block; font-size: 11px; font-weight: 700; border-radius: 6px; padding: 0 6px; margin-right: 4px; background: var(--cs); color: var(--c); white-space: nowrap; }
.legend { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 2px; }
.filter button { font: inherit; font-size: 13px; border: 1px solid var(--border); background: var(--card); color: var(--fg); border-radius: 999px; padding: 2px 10px; cursor: pointer; }
.filter button[aria-pressed="true"] { border-color: var(--c, var(--accent)); background: var(--cs, var(--soft)); color: var(--c, var(--fg)); font-weight: 600; }
.out-accept { color: var(--ok); font-weight: 600; } .out-revise, .out-block, .out-blocked { color: var(--bad); font-weight: 600; }
details > summary { cursor: pointer; font-weight: 600; }
.mono { font: 12px ui-monospace, SFMono-Regular, Consolas, monospace; }
tr.hl td { background: var(--cs); }
footer { margin-top: 28px; color: var(--muted); font-size: 13px; }
@media (max-width: 640px) { .q { font-size: 16px; } .verdict { font-size: 18px; } .hide-sm { display: none; } }
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Cleris replay</h1>
  <p class="sub">Live runs of Cleris, the MRO clearance assistant, saved and shown as they happened. Each brief was built from live Zetaris queries over AWS, the Meterless World Model and H-MEM, and an NVIDIA Nemotron critic reached through Meterless Nightshift; deterministic gates checked every claim before the engineer sees it. Nothing runs here: this page only reads the saved runs.</p>
  <div class="links" id="links"></div>
  <div id="services"></div>
</header>
<section class="card" id="static-summary">/*STATIC*/</section>
<h2 class="band">Sponsor technology across these runs</h2>
<div class="sponsors" id="sponsors"></div>
<h2 class="band">Choose a run</h2>
<nav class="cases" id="cases" aria-label="Recorded runs"></nav>
<main id="case"></main>
<footer id="footer"></footer>
</div>
<script id="data" type="application/json">/*DATA*/</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
document.getElementById("static-summary").hidden = true;      // the interactive page below replaces it
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (x, d = 1) => (x === null || x === undefined || x === "") ? "–" : (typeof x === "number" ? (Number.isInteger(x) ? String(x) : x.toFixed(d)) : esc(x));
const when = iso => { if (!iso) return "–"; const t = new Date(iso); return t.toLocaleString("en-GB", {day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC"}) + " UTC"; };
const words = s => esc(String(s ?? "").replaceAll("_", " "));
const short = m => esc(String(m || "").split("/").pop());
const SP = {
  zet: "Zetaris", wm: "Meterless World Model", hm: "Meterless H-MEM", ns: "Meterless Nightshift", nv: "NVIDIA Nemotron", oth: "Cleris"
};
// Which technology a trace line used.
function lineKind(l) {
  if (l.action === "zetaris_query") return "zet";
  if (l.action === "context_call") return (l.input_summary || "").startsWith("H-MEM") ? "hm" : "wm";
  if (l.model) return "nv";
  return "oth";
}
// Which technology an evidence record came from, by its source.
function evKind(e) {
  const s = (e && e.source) || "";
  if (s.startsWith("zetaris")) return "zet";
  if (/^(meterless|sqlite):(world|wm)/.test(s)) return "wm";
  if (/^(meterless|sqlite):(hmem|cmms:work_orders)/.test(s) || /hmem/.test(s)) return "hm";
  if (s.startsWith("nightshift") || s.startsWith("llm")) return "nv";
  return "oth";
}
const critic = l => { const m = /(\d+) challenge\(s\): (\d+) confirmed by code, (\d+) refuted/.exec(l.output_summary || ""); return m ? {raised: +m[1], confirmed: +m[2], refuted: +m[3]} : null; };

document.getElementById("links").innerHTML =
  `<a href="${esc(D.repo)}">Source on GitHub</a><a href="${esc(D.video)}">Demo video (3:52)</a>` +
  `<a href="${esc(D.repo)}#check-the-live-path-without-credentials">Check the live path without credentials</a>` +
  `<a href="${esc(D.repo)}/tree/main/docs/replay/data">Raw run files</a>`;

if (D.health) {
  const h = D.health.health;
  const chips = (h.components || []).map(c => `<span class="chip ${c.state === "ok" ? "ok" : c.state === "warn" ? "warn" : ""}" title="${esc((c.details || []).join("; "))}"><span class="dot"></span>${esc(c.label)}: ${esc(c.chip || c.summary)}</span>`).join("");
  const parts = ((h.components || []).find(c => c.key === "zetaris") || {}).parts || [];
  const sources = parts.map(p => `<span class="chip ${p.state === "ok" ? "ok" : "warn"}"><span class="dot"></span>${esc(p.name)} (${esc(p.kind)}): ${esc(p.summary)}</span>`).join("");
  document.getElementById("services").innerHTML =
    `<div class="note">Services right after these runs (GET /health, ${when(D.health.captured_at)}):</div><div class="strip">${chips}</div><div class="strip">${sources}</div>`;
}

// Totals across all runs, one tile per sponsor technology.
(() => {
  const all = D.runs.flatMap(r => r.trace);
  const zet = all.filter(l => l.action === "zetaris_query");
  const live = zet.filter(l => (l.output_summary || "").endsWith("live Zetaris SQL")).length;
  const sources = [...new Set(zet.flatMap(l => (l.output_summary.split(" · ")[1] || "").split(" + ").map(t => t.split(".")[0])))].filter(Boolean).sort();
  const rows = zet.reduce((n, l) => n + (+((/ (\d+) rows?/.exec(l.output_summary) || [0, 0])[1])), 0);
  const wm = all.filter(l => lineKind(l) === "wm"), hm = all.filter(l => lineKind(l) === "hm");
  const calls = D.runs.flatMap(r => r.result.model_calls || []);
  const liveCalls = calls.filter(c => !c.recorded);
  const models = [...new Set(calls.map(c => short(c.model)))];
  const ch = all.map(critic).filter(Boolean).reduce((a, c) => ({raised: a.raised + c.raised, confirmed: a.confirmed + c.confirmed, refuted: a.refuted + c.refuted}), {raised: 0, confirmed: 0, refuted: 0});
  const apps = [...new Set(all.filter(l => l.model).map(l => l.agent_name))];
  document.getElementById("sponsors").innerHTML = `
    <div class="sp c-zet"><div class="who">Zetaris</div><div class="n">${zet.length}</div><div class="what">live SQL reads (${live} of ${zet.length}), ${rows.toLocaleString("en")} rows from ${sources.map(esc).join(", ")}: three AWS RDS Postgres databases and an S3 file. The only data layer: whether parts arrive in time is one join of S3 failure modes to stock.</div></div>
    <div class="sp c-wm"><div class="who">Meterless World Model</div><div class="n">${wm.length}</div><div class="what">graph queries. The map of the engine decides which tasks are eligible for the fault; a rule check fails any task it does not list.</div></div>
    <div class="sp c-hm"><div class="who">Meterless H-MEM</div><div class="n">${hm.length}</div><div class="what">memory searches over 200 past removals: similar cases and the warning the same fault gave. Each sign-off adjusts their trust.</div></div>
    <div class="sp c-ns"><div class="who">Meterless Nightshift</div><div class="n">${calls.length}</div><div class="what">model calls, all through Nightshift, the only AI provider. Its app policy pins the critic to a Nemotron lane.</div></div>
    <div class="sp c-nv"><div class="who">NVIDIA Nemotron</div><div class="n">${liveCalls.length}</div><div class="what">live replies from ${models.join(", ") || "Nemotron"}, which tries to disprove each brief: ${ch.raised} checkable challenges, ${ch.confirmed} confirmed by code, ${ch.refuted} refuted. Code decides.</div></div>`;
})();

function evChips(ids, ev, scope) {
  return (ids || []).filter(Boolean).map(id => { const k = evKind(ev[id]); return `<button class="ev c-${k}" data-ev="${esc(id)}" data-scope="${esc(scope)}" title="${esc(evLabel(ev[id]))}: ${esc((ev[id] || {}).summary || "")}">${esc(id)}</button>`; }).join("");
}
const OTHER = {model: "Cleris model", asset_pack: "Asset pack", case_file: "Case file", inspection: "Inspection finding", snapshot: "Snapshot"};
const evLabel = e => { const k = evKind(e); return k === "oth" ? (OTHER[((e && e.source) || "").split(":")[0]] || OTHER[(e && e.kind) || ""] || "Cleris") : SP[k]; };
function evDetail(id, ev) {
  const e = ev[id];
  if (!e) return `<div class="evbox">${esc(id)}: not in this run's evidence</div>`;
  const k = evKind(e);
  return `<div class="evbox c-${k}"><span class="badge">${esc(evLabel(e))}</span> <span class="mono">${esc(id)}</span> · ${words(e.kind)} · <span class="mono">${esc(e.source)}</span> · from ${words(e.produced_by)}${e.degraded ? ` · <strong>degraded</strong>` : ""}<br>${esc(e.summary)}</div>`;
}
const badge = k => `<span class="badge c-${k}">${esc(k === "nv" ? "Nemotron via Nightshift" : SP[k].replace("Meterless ", ""))}</span>`;

function render(run) {
  const r = run.result, rec = r.recommendation || {}, f = r.findings || {}, ev = r.evidence || {};
  const t0 = run.trace.length ? new Date(run.trace[0].timestamp) : null;
  const zet = run.trace.filter(l => lineKind(l) === "zet"), wm = run.trace.filter(l => lineKind(l) === "wm"), hm = run.trace.filter(l => lineKind(l) === "hm");
  const nv = run.trace.filter(l => l.model);
  const live = !r.mode.sample_mode && r.mode.data === "live";
  const fm = (f.fault_modes || [])[0];
  const evByKind = Object.values(ev).reduce((a, e) => { const k = evKind(e) + "|" + evLabel(e); a[k] = (a[k] || 0) + 1; return a; }, {});
  const out = [];

  out.push(`<section class="card"><p class="q">“${esc(r.question)}”</p>
    <div class="note">${live ? "Live run" : "Sample-mode run"} ${esc(r.run_id)} · ${when(run.trace[0] && run.trace[0].timestamp)} · ${fmt(r.duration_s, 2)} s · data ${esc(r.mode.data)} · case memory ${esc(r.mode.context_store)} · degraded: ${esc((r.degraded || []).join(", ") || "none")} · input <a href="${esc(D.repo)}/blob/main/${esc(run.input)}">${esc(run.input)}</a></div></section>`);

  const status = r.status === "needs_review" ? "Waiting for the engineer's sign-off" : words(r.status);
  out.push(`<section class="card"><h2>Decision brief</h2><p class="verdict">${esc(rec.action || (r.intent && r.intent.clarification) || "No recommendation")}</p>
    <div class="grid">
      <div class="kv"><div class="k">Deadline</div><div class="v">${fmt(rec.deadline)}</div><div class="s">from the remaining-life lower bound</div></div>
      <div class="kv"><div class="k">Hangar slot</div><div class="v">${fmt(rec.slot)}</div><div class="s">first free slot that fits (Zetaris)</div></div>
      <div class="kv"><div class="k">Confidence</div><div class="v">${fmt(rec.confidence)}</div></div>
      <div class="kv"><div class="k">Status</div><div class="v" style="font-size:15px">${status}</div></div>
    </div>
    <div class="note" style="margin-top:10px">Where the ${Object.keys(ev).length} evidence records behind this brief came from:</div><div class="legend">${Object.entries(evByKind).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span class="badge c-${k.split("|")[0]}">${esc(k.split("|")[1])}: ${n}</span>`).join("")}</div></section>`);

  // Sponsor technology in this run: what each one returned, call by call.
  const zrows = zet.map(l => { const p = l.output_summary.split(" · "); return `<tr><td>${esc(p[0])}</td><td class="mono">${esc(p[1] || "")}</td><td>${esc(p[2] || "")}</td><td class="mono">${esc(p[3] || "")}</td><td class="hide-sm">${esc(p[4] || "")}</td></tr>`; }).join("");
  const crow = l => `<tr><td>${words(l.agent_name)}</td><td>${esc(l.input_summary)}</td><td>${esc((l.output_summary.split(" · ")[2]) || l.output_summary)}</td><td class="mono hide-sm">${fmt(l.duration_s, 3)} s</td></tr>`;
  const nrows = nv.map(l => { const c = critic(l); return `<tr><td>${words(l.agent_name)} · ${words(l.action)}</td><td class="mono">${short(l.model)}${l.recorded ? " (recorded)" : " (live)"}</td><td>${c ? `${c.raised} challenges: <strong>${c.confirmed} confirmed by code</strong>, ${c.refuted} refuted` : ""}<div class="note">${esc(l.output_summary)}</div></td><td class="mono hide-sm">${fmt(l.duration_s, 1)} s</td></tr>`; }).join("");
  out.push(`<section class="card"><h2>Sponsor technology in this run</h2>
    <div class="panel c-zet"><div class="ph"><strong>Zetaris</strong><span class="note">${zet.length} SQL reads through the AI Data Gateway REST API, read-only (SELECT guard)</span></div>
      <div class="scroll"><table><tr><th>What for</th><th>Tables</th><th>Rows</th><th>Took</th><th class="hide-sm">Mode</th></tr>${zrows || `<tr><td colspan="5">none</td></tr>`}</table></div></div>
    <div class="panel c-wm"><div class="ph"><strong>Meterless World Model</strong><span class="note">${wm.length} graph queries: eligible tasks for the fault, sensors to components, documents to tasks</span></div>
      <div class="scroll"><table><tr><th>Agent</th><th>Query</th><th>Answer</th><th class="hide-sm">Took</th></tr>${wm.map(crow).join("") || `<tr><td colspan="4">none</td></tr>`}</table></div></div>
    <div class="panel c-hm"><div class="ph"><strong>Meterless H-MEM</strong><span class="note">${hm.length} memory searches over past removals</span></div>
      <div class="scroll"><table><tr><th>Agent</th><th>Search</th><th>Answer</th><th class="hide-sm">Took</th></tr>${hm.map(crow).join("") || `<tr><td colspan="4">none (no fault mode to compare)</td></tr>`}</table></div></div>
    <div class="panel c-nv"><div class="ph"><strong>NVIDIA Nemotron via Meterless Nightshift</strong><span class="note">the only AI provider; code confirms or refutes every checkable challenge</span></div>
      <div class="scroll"><table><tr><th>Agent</th><th>Model</th><th>Outcome</th><th class="hide-sm">Took</th></tr>${nrows || `<tr><td colspan="4">no model call</td></tr>`}</table></div></div>
  </section>`);

  const a = f.rul_ml || {}, b = f.rul_trend || {}, an = f.anomaly || {};
  out.push(`<section class="card"><h2>Key numbers (computed in code, each with its evidence)</h2><div class="grid">
      <div class="kv"><div class="k">Remaining life A (LightGBM, calibrated)</div><div class="v">${fmt(a.rul)} cycles</div><div class="s">likely ${fmt(a.lower)}–${fmt(a.upper)} · ${evChips([a.evidence_id], ev, "num")}</div></div>
      <div class="kv"><div class="k">Remaining life B (health-index trend)</div><div class="v">${fmt(b.rul)} cycles</div><div class="s">likely ${fmt(b.lower)}–${fmt(b.upper)} · ${evChips([b.evidence_id], ev, "num")}</div></div>
      <div class="kv"><div class="k">Fault mode</div><div class="v" style="font-size:15px">${fm ? words(fm.failure_mode) : "–"}</div><div class="s">${fm ? `p = ${fmt(fm.probability, 2)} · ${evChips([fm.evidence_id], ev, "num")}` : ""}</div></div>
      <div class="kv"><div class="k">Health index vs fleet</div><div class="v">${an.flagged ? "flagged" : "not flagged"}</div><div class="s">${an.health_index !== undefined ? `${fmt(an.health_index, 3)} vs median ${fmt(an.fleet_median, 3)} · ` : ""}${evChips([an.evidence_id], ev, "num")}</div></div>
      <div class="kv c-hm"><div class="k">Similar past removals (H-MEM)</div><div class="v">${(f.similar_cases || []).length}</div><div class="s">${evChips(f.similar_cases || [], ev, "num")}</div></div>
    </div><div id="evbox-num"></div></section>`);

  if ((r.escalations || []).length) {
    out.push(`<section class="card"><h2>Escalations the engineer must acknowledge</h2>${r.escalations.map(e => `
      <div class="esc ${e.severity === "urgent" ? "urgent" : ""}"><div class="rule">${words(e.severity)} · ${words(e.rule)}</div><div>${esc(e.message)}</div>
      ${(e.actions || []).length ? `<ul>${e.actions.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}<div>${evChips(e.evidence_ids, ev, "esc")}</div></div>`).join("")}<div id="evbox-esc"></div></section>`);
  }

  const comp = rec.compliance || [], wp = rec.work_package, parts = rec.parts || [];
  if (comp.length || wp || parts.length) {
    let body = "";
    if (comp.length) body += `<h2>How each mandate is complied with</h2>${comp.map(c => `<div class="claim"><strong>${esc(c.doc_id)} ${esc(c.section)}</strong> (${words(c.doc_type)}): ${esc(c.text || c.method)} ${evChips(c.evidence_ids, ev, "plan")}</div>`).join("")}`;
    if (wp) body += `<h3>Work package: ${fmt(wp.manhours)} manhours, ${fmt(wp.rii)} required inspection items</h3>${(wp.sections || []).map(s => `<div class="claim"><strong>${esc(s.title)}</strong> · ${esc(s.when || "")} · ${(s.tasks || []).length} task cards · ${esc(s.skill || "")}</div>`).join("")}${wp.note ? `<div class="note">${esc(wp.note)}</div>` : ""}`;
    if (parts.length) body += `<h3>Parts (stock and lead time from Zetaris)</h3><div class="scroll"><table><tr><th>Part number</th><th>Qty</th><th>On hand</th><th>Lead time</th><th class="hide-sm">Evidence</th></tr>${parts.map(p => `<tr><td class="mono">${esc(p.part_number)}</td><td>${fmt(p.qty)}</td><td>${p.available ? "yes" : "no"}</td><td>${fmt(p.lead_time_days)} d</td><td class="hide-sm">${evChips(p.evidence_ids, ev, "plan")}</td></tr>`).join("")}</table></div>`;
    out.push(`<section class="card">${body}<div id="evbox-plan"></div></section>`);
  }

  if ((f.inspections || []).length) {
    out.push(`<section class="card"><h2>Inspection findings, compared with the manual's limit in code</h2><div class="scroll"><table><tr><th>Finding</th><th>Where</th><th>Result</th><th>Limit</th><th>Action</th></tr>${f.inspections.map(i => `<tr><td>${words(i.kind)} ${esc(i.finding_id)} ${evChips([i.evidence_id, i.limit_evidence_id], ev, "ins")}</td><td>${esc(i.location)} · cycle ${fmt(i.cycle)}</td><td><strong>${words(i.result)}</strong></td><td>${i.limit ? `${esc(i.limit.name)}: serviceable ${fmt(i.limit.serviceable, 2)}, reject ${fmt(i.limit.reject, 2)} ${esc(i.limit.unit)} (${esc(i.reference)})` : "–"}</td><td>${words(i.action)}</td></tr>`).join("")}</table></div><div id="evbox-ins"></div></section>`);
  }

  const claims = [...(rec.rationale || []), ...(f.claims || [])];
  out.push(`<section class="card"><h2>Why: every claim and the evidence it cites (${claims.length} claims, ${Object.keys(ev).length} evidence records)</h2>
    <div class="note">Evidence IDs are coloured by where they came from. Select one to see its source. Rule checks fail any claim whose evidence does not resolve or does not contain its numbers.</div>
    ${claims.map((c, i) => `<div class="claim">${esc(c.text)} ${evChips(c.evidence_ids, ev, "c" + i)}<div id="evbox-c${i}"></div></div>`).join("")}</section>`);

  out.push(`<section class="card"><h2>Gates: what checked the brief</h2><div class="scroll"><table><tr><th>Round</th><th>Gate</th><th>Outcome</th><th>Notes</th></tr>${(r.verdicts || []).map(v => {
    const routed = (v.routes || []).map(x => `<div><strong>sent to ${words(x.target)}</strong> (${words(x.kind)}): ${esc(x.gap)}</div>`).join("");
    const notes = (v.gaps || []).filter(g => !(v.routes || []).some(x => x.gap === g)).map(g => `<div>${v.outcome === "accept" ? "<em>Model note for the engineer:</em> " : ""}${esc(g)}</div>`).join("");
    return `<tr><td>${fmt(v.round)}</td><td>${words(v.gate)}${v.model ? `<br>${badge("nv")}<span class="mono">${short(v.model)}${v.recorded ? " (recorded)" : ""}</span>` : ""}</td><td class="out-${esc(v.outcome)}">${esc(v.outcome)}</td><td>${routed + notes || "no gaps"}</td></tr>`;
  }).join("")}</table></div></section>`);

  const kinds = ["all", "zet", "wm", "hm", "nv"];
  out.push(`<section class="card"><details open><summary>Timeline: every trace line (${run.trace.length})</summary>
    <div class="strip filter" id="filter">${kinds.map(k => `<button class="${k === "all" ? "" : "c-" + k}" data-k="${k}" aria-pressed="${k === "all"}">${k === "all" ? "All agents" : esc(SP[k])}</button>`).join("")}</div>
    <div class="scroll"><table id="timeline">
    <tr><th>t (s)</th><th>Agent</th><th>Action</th><th>What happened</th><th class="hide-sm">Took</th></tr>
    ${run.trace.map(l => { const k = lineKind(l); return `<tr data-k="${k}" class="${k === "oth" ? "" : "hl c-" + k}"><td class="mono">${t0 ? ((new Date(l.timestamp) - t0) / 1000).toFixed(2) : ""}</td><td>${words(l.agent_name)}</td><td>${k === "oth" ? "" : badge(k)}${words(l.action)}${l.target_agent ? ` → ${words(l.target_agent)}` : ""}</td><td>${esc(l.output_summary)}</td><td class="hide-sm mono">${fmt(l.duration_s, 2)}</td></tr>`; }).join("")}
    </table></div></details></section>`);

  out.push(`<section class="card"><details><summary>Decision log (${(r.decision_log || []).length} entries; the engineer's signature is appended at sign-off)</summary><div class="scroll" style="margin-top:8px"><table>
    <tr><th>Who</th><th>Decision</th><th>Why</th></tr>${(r.decision_log || []).map(e => `<tr><td>${words(e.actor_type)}: ${words(e.actor)}</td><td>${esc(e.decision)}</td><td>${esc(e.rationale)} ${evChips(e.evidence_ids, ev, "log")}</td></tr>`).join("")}</table></div><div id="evbox-log"></div></details></section>`);

  document.getElementById("case").innerHTML = out.join("");
}

let current = null;
function select(key) {
  current = D.runs.find(r => r.key === key) || D.runs[0];
  document.querySelectorAll("nav.cases button").forEach(b => b.setAttribute("aria-current", String(b.dataset.key === current.key)));
  render(current);
  if (location.hash !== "#" + current.key) history.replaceState(null, "", "#" + current.key);
}

document.getElementById("cases").innerHTML = D.runs.map(run => {
  const r = run.result, act = (r.recommendation && r.recommendation.action) || "";
  return `<button data-key="${esc(run.key)}"><div class="eng">${esc((r.asset || {}).asset_id || "")}</div><div class="lab">${esc(run.label)}</div><div class="act">${esc(act.split(";")[0])}</div></button>`;
}).join("");
document.getElementById("cases").addEventListener("click", e => { const b = e.target.closest("button[data-key]"); if (b) select(b.dataset.key); });
document.getElementById("case").addEventListener("click", e => {
  const f = e.target.closest("#filter button");
  if (f) {
    document.querySelectorAll("#filter button").forEach(b => b.setAttribute("aria-pressed", String(b === f)));
    document.querySelectorAll("#timeline tr[data-k]").forEach(tr => { tr.style.display = f.dataset.k === "all" || tr.dataset.k === f.dataset.k ? "" : "none"; });
    return;
  }
  const b = e.target.closest("button.ev"); if (!b || !current) return;
  const box = document.getElementById("evbox-" + b.dataset.scope); if (!box) return;
  const open = box.dataset.open === b.dataset.ev;
  box.innerHTML = open ? "" : evDetail(b.dataset.ev, current.result.evidence || {});
  box.dataset.open = open ? "" : b.dataset.ev;
});
window.addEventListener("hashchange", () => select(location.hash.slice(1)));
select(location.hash.slice(1));

const n = D.runs.length, liveRuns = D.runs.filter(r => !r.result.mode.sample_mode).length;
document.getElementById("footer").innerHTML =
  `${n} runs, ${liveRuns} of them live, recorded with <span class="mono">uv run python -m tools.replay --record</span> against Zetaris (AWS RDS Postgres and S3), the Meterless context service (World Model, H-MEM) and Meterless Nightshift (NVIDIA Nemotron); this page is built by <span class="mono">uv run task replay</span>. ` +
  `To run the same inputs yourself with no credentials: <span class="mono">SAMPLE_MODE=true uv run python run.py --input input_examples/critical_hpc.json</span>. ` +
  `Telemetry is the NASA C-MAPSS simulated dataset; maintenance, spares and documents are synthetic. Advisory decision support, not for operational use.`;
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
