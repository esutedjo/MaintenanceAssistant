"""What the committed live runs show, for a reader without credentials. Run: uv run task live-evidence

The live path needs Zetaris (with its RDS and S3 sources), the Meterless context service and Nightshift, so a judge
cannot run it. These files were recorded against those services and are committed in trace_examples/:

- trace-live-*.jsonl: briefs run in live mode. Each Zetaris read has its own line (purpose, tables, rows, latency,
  "live Zetaris SQL"), each World Model or H-MEM call has its own line (backend), and the critic line names the model
  that answered through Nightshift (`recorded: false`: a live call, not a sample-mode replay).
- health-live.json: GET /health in live mode, read right after one more live brief in the same process, so it names
  the model the critic lane served. No credentials, tokens, hosts or connection strings.

This prints what they show and checks that each trace reads as a live run: every Zetaris line is a live SQL read,
every World Model and H-MEM call went to the Meterless service, and the critic was answered live by a Nemotron model.
Exit 1 if a check fails. Needs no credentials and no network.

  uv run task live-evidence
  uv run python -m tools.live_evidence --capture-health     # live mode only: rewrites health-live.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from settings import ROOT, Settings, get_settings

TRACES = ROOT / "trace_examples"
HEALTH = TRACES / "health-live.json"
QUESTION = "What should I do about FD001-A031?"
LIVE_SQL = "live Zetaris SQL"
CHALLENGES = re.compile(r"(\d+) challenge\(s\): (\d+) confirmed by code, (\d+) refuted")
# Settings whose values must never reach a committed file; the check names them, never prints their values.
SENSITIVE = re.compile(r"(_url|_host|_user|_org_id|_password|_key|_token)$")


def read_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _when(line: dict) -> datetime:
    return datetime.fromisoformat(line["timestamp"])


def _read(line: dict) -> dict:
    """"asset registry for FD001-A031 · cmms_pg.assets · 1 row · 0.38 s · live Zetaris SQL" -> its parts."""
    parts = line["output_summary"].split(" · ")
    count = parts[2].split()[0] if len(parts) > 2 else ""
    return {"tables": parts[1].split(" + ") if len(parts) > 1 else [], "rows": int(count) if count.isdigit() else 0,
            "seconds": float(line.get("duration_s") or 0.0), "mode": parts[-1]}


def summarise(path: Path) -> dict:
    """One live trace: the engine, when it ran, its Zetaris reads, its Meterless calls, the critic's model, and what
    keeps it from reading as a live run (`problems`, empty when it does)."""
    lines = read_trace(path)
    name = re.search(r"trace-live-(.+)-(FD\d{3}-A\d{3})\.jsonl$", path.name)
    reads = [_read(line) for line in lines if line["action"] == "zetaris_query"]
    calls = [line for line in lines if line["action"] == "context_call"]
    critic = [line for line in lines if line["agent_name"] == "critic" and line.get("model")]
    problems = ([] if reads else ["no Zetaris read"]) + [f"Zetaris read not live: {r['mode']}" for r in reads
                                                         if r["mode"] != LIVE_SQL]
    problems += [] if calls else ["no World Model or H-MEM call"]
    problems += sorted({f"context call on {c['target_agent']}, not the Meterless service" for c in calls
                        if c["target_agent"] != "meterless"})
    problems += [] if critic else ["no critic line that names a model"]
    problems += [f"critic answered by {c['model']}, not a Nemotron model" for c in critic
                 if "nemotron" not in c["model"].lower()]
    problems += ["critic reply replayed from a recording, not a live call" for c in critic if c.get("recorded")]
    return {
        "file": path.name, "case": name.group(1) if name else "?", "engine": name.group(2) if name else "?",
        "run_id": lines[0]["run_id"] if lines else "?",
        "start": _when(lines[0]) if lines else None, "end": _when(lines[-1]) if lines else None,
        "reads": reads, "sources": sorted({t.split(".")[0] for r in reads for t in r["tables"]}),
        "calls": Counter("World Model" if c["input_summary"].startswith("World Model") else
                         "H-MEM" if c["input_summary"].startswith("H-MEM") else "other" for c in calls),
        "models": sorted({c["model"] for c in critic}),
        "challenges": [tuple(map(int, m.groups())) for c in critic if (m := CHALLENGES.search(c["output_summary"]))],
        "problems": problems,
    }


def _run_lines(run: dict) -> list[str]:
    reads, calls = run["reads"], run["calls"]
    span = (f"{run['start']:%d %b %Y %H:%M:%S}-{run['end']:%H:%M:%S} UTC" if run["start"] else "no lines")
    seconds = [r["seconds"] for r in reads]
    out = [f"{run['engine']} ({run['case']}), run {run['run_id']}, {span}",
           f"  Zetaris    {len(reads)} live SQL reads, {sum(r['rows'] for r in reads)} rows"
           + (f", {min(seconds):.2f}-{max(seconds):.2f} s each" if seconds else "")
           + f"; sources {', '.join(run['sources']) or 'none'}",
           f"  Meterless  {sum(calls.values())} context-service calls: "
           + ", ".join(f"{n} {kind}" for kind, n in sorted(calls.items()))]
    critic = f"  Nightshift critic answered by {', '.join(run['models']) or 'no model'}"
    for total, confirmed, refuted in run["challenges"]:
        critic += f"; {total} challenges, {confirmed} confirmed by code, {refuted} refuted"
    out.append(critic)
    out.append("  Check      " + ("reads as a live run" if not run["problems"] else
                                  "FAILED: " + "; ".join(run["problems"])))
    return out


def health_problems(record: dict) -> list[str]:
    health, brief = record.get("health") or {}, record.get("brief") or {}
    states = {c["key"]: c for c in health.get("components") or []}
    problems = [f"{key} is {states[key]['state'] if key in states else 'missing'}: "
                f"{states[key]['summary'] if key in states else 'no component'}"
                for key in ("mode", "zetaris", "context_service", "nightshift")
                if states.get(key, {}).get("state") != "ok"]
    if "Nemotron" not in (states.get("nightshift") or {}).get("summary", ""):
        problems.append("Nightshift does not name a Nemotron model")
    if brief.get("degraded"):
        problems.append(f"the brief ran degraded: {', '.join(brief['degraded'])}")
    if not any("nemotron" in (c.get("model") or "").lower() and not c.get("recorded")
               for c in brief.get("model_calls") or []):
        problems.append("the brief has no live Nemotron call")
    return problems


def _health_lines(path: Path) -> tuple[list[str], list[str]]:
    if not path.exists():
        return [f"No live /health snapshot at {path.relative_to(ROOT).as_posix()}"], ["no /health snapshot"]
    record = json.loads(path.read_text(encoding="utf-8"))
    health, brief = record["health"], record["brief"]
    out = [f"Live GET /health, read at {record['captured_at']} ({path.relative_to(ROOT).as_posix()})",
           f"  overall {health['overall']}"]
    for component in health["components"]:
        out.append(f"  {component['label']}: {component['summary']}")
        out += [f"    {part['name']} ({part['kind']}): {part['summary']}" for part in component.get("parts") or []]
    models = ", ".join(sorted({c["model"] for c in brief["model_calls"]})) or "no model"
    out.append(f"  Brief run just before, in the same process: {brief['question']} -> {brief['status']} in "
               f"{brief['duration_s']} s, run {brief['run_id']}; {brief['zetaris_reads']} live Zetaris reads, "
               f"{brief['context_calls']} Meterless calls, critic {models}; degraded "
               f"{', '.join(brief['degraded']) or 'none'}")
    problems = health_problems(record)
    out.append("  Check      " + ("all live services answered" if not problems else
                                  "FAILED: " + "; ".join(problems)))
    return out, problems


def report(directory: Path = TRACES, health: Path = HEALTH) -> int:
    from tools.status import SOURCE_PROBES

    runs = sorted((summarise(path) for path in directory.glob("trace-live-*.jsonl")),
                  key=lambda r: r["start"] or datetime.max.replace(tzinfo=UTC))
    lines = [(f"Live runs committed in {directory.relative_to(ROOT).as_posix()}/ "
              "(recorded against Zetaris, the Meterless context service and Nightshift):"), ""]
    for run in runs:
        lines += _run_lines(run) + [""]
    kinds = {name: kind for name, kind, _ in SOURCE_PROBES}
    sources = sorted({s for run in runs for s in run["sources"]})
    models = sorted({m for run in runs for m in run["models"]})
    lines.append(f"Together: {len(runs)} runs, {sum(len(r['reads']) for r in runs)} live Zetaris reads across "
                 + ", ".join(f"{s} ({kinds.get(s, '?')})" for s in sources)
                 + f"; {sum(sum(r['calls'].values()) for r in runs)} Meterless calls; critic answered by "
                 + (", ".join(models) or "no model") + ".")
    health_lines, health_failed = _health_lines(health)
    lines += [""] + health_lines
    failed = [r for r in runs if r["problems"]]
    lines += ["", "No live traces found." if not runs else
              f"{len(failed)} of {len(runs)} traces failed the check." if failed else
              "Every trace reads as a live run." + (" The /health snapshot did not pass." if health_failed else "")]
    print("\n".join(lines))
    return 1 if not runs or failed or health_failed else 0


def leaks(text: str, settings: Settings) -> list[str]:
    """Names (never values) of the settings whose value appears in `text`, plus any URL or AWS host name in it."""
    found = []
    for name in type(settings).model_fields:
        if not SENSITIVE.search(name):
            continue
        value = getattr(settings, name)
        value = value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")
        if len(value) >= 6 and value in text:
            found.append(name.upper())
    if "://" in text:
        found.append("a URL")
    if re.search(r"amazonaws\.com|\.rds\.", text):
        found.append("an AWS host name")
    return found


def capture_health(settings: Settings, path: Path = HEALTH) -> int:
    """Run one live brief, then read GET /health in the same process (so Nightshift names the model the critic lane
    just served) and write both to `path`. Refuses to write if a setting's value, a URL or an AWS host is in it."""
    if settings.sample_mode or settings.zetaris_mode != "live":
        print("--capture-health needs live mode (SAMPLE_MODE off, ZETARIS_MODE=live): it runs one live brief and "
              "reads GET /health.")
        return 2
    from graph.runner import execute_run, new_run_id
    from run import health_payload

    outcome = execute_run(QUESTION, settings, run_id=new_run_id())
    health = health_payload(settings)
    for _ in range(3):      # the first read in a process can still be checking
        if health.get("checked_at"):
            break
        time.sleep(2)
        health = health_payload(settings)
    result = outcome.result
    trace = read_trace(ROOT / result["trace_file"])
    record = {
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "how": "uv run python -m tools.live_evidence --capture-health: one live brief, then GET /health "
               "(run.py health_payload) in the same process",
        "brief": {"question": QUESTION, "run_id": result["run_id"], "status": result["status"],
                  "degraded": result["degraded"], "duration_s": result["duration_s"],
                  "zetaris_reads": sum(1 for line in trace if line["action"] == "zetaris_query"
                                       and line["output_summary"].endswith(LIVE_SQL)),
                  "context_calls": sum(1 for line in trace if line["action"] == "context_call"
                                       and line["target_agent"] == "meterless"),
                  "model_calls": result["model_calls"]},
        "health": health,
    }
    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    leaked = leaks(text, settings)
    if leaked:
        print(f"Not written: the snapshot would contain {', '.join(leaked)}.")
        return 1
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT).as_posix()}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise and check the committed live traces and /health "
                                                 "snapshot; no credentials or network needed.")
    parser.add_argument("--capture-health", action="store_true",
                        help="live mode only: run one live brief, read GET /health and rewrite health-live.json")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")
    if args.capture_health and (code := capture_health(get_settings())):
        return code
    return report()


if __name__ == "__main__":
    sys.exit(main())
