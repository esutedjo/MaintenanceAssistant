"""What Nemotron adds on its own: corrupt the golden briefs with each seeded fault, bypass the deterministic gates,
and ask the LLM critic through Nightshift. A fault counts as caught when the model raises a challenge that code
confirms; a clean brief counts as a false veto when a challenge is confirmed on it. Needs Nightshift (live).
Run: uv run task eval-llm  (about 50 model calls)"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from agents.critic import _llm_gaps, checked_challenges
from data.db import load_pack
from evaluation.faults import FAULTS
from graph.runner import execute_run
from settings import ROOT, Settings, get_settings

GOLDEN = ["FD001-A001", "FD001-A031", "FD003-A100", "FD001-A070"]
OUT = ROOT / "evaluation"


def _ask(case, ctx) -> dict:
    start = time.monotonic()
    gaps, model, note, challenges, _ = _llm_gaps(case, ctx)
    confirmed, refuted = checked_challenges(case, ctx, challenges, model)
    return {"model": model, "note": note, "seconds": round(time.monotonic() - start, 1), "free_text_gaps": len(gaps),
            "challenges": len(challenges), "confirmed": confirmed, "refuted": refuted}


def main() -> int:
    logging.disable(logging.INFO)
    pack = load_pack()
    live = get_settings().model_copy(update={"sample_mode": False, "run_timeout_s": 600.0})
    with tempfile.TemporaryDirectory() as tmp:
        sample = Settings(_env_file=None, sample_mode=True, context_sqlite_path=Path(tmp) / "context.sqlite")
        cases = {a: execute_run(f"What is wrong with {a}, how long do I have, and what should I do?", sample,
                                run_id=f"llm-eval-{a}").case for a in GOLDEN}
    for a in GOLDEN:
        (ROOT / "logs" / f"trace-llm-eval-{a}.jsonl").unlink(missing_ok=True)
    rows = []
    for asset, case in cases.items():
        ctx = SimpleNamespace(settings=live, pack=pack, started=time.monotonic())
        rows.append({"asset": asset, "fault": "clean", **_ask(case, ctx)})
        for name, fault in FAULTS.items():
            faulty = fault(case, pack)
            if faulty is None:
                continue
            ctx.started = time.monotonic()
            rows.append({"asset": asset, "fault": name, **_ask(faulty, ctx)})
            print(f"  {asset} {name}: {len(rows[-1]['confirmed'])} confirmed, {len(rows[-1]['refuted'])} refuted",
                  flush=True)
    seeded = [r for r in rows if r["fault"] != "clean"]
    answered = [r for r in seeded if r["model"] and not (r["note"] or "").startswith("llm_critic_skipped")]
    clean = [r for r in rows if r["fault"] == "clean"]
    models = sorted({r["model"] for r in rows if r["model"]})
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "models": models,
        "seeded": len(seeded), "answered": len(answered),
        "caught_by_confirmed_challenge": sum(1 for r in answered if r["confirmed"]),
        "flagged_in_free_text": sum(1 for r in answered if r["free_text_gaps"]),
        "challenges_raised": sum(r["challenges"] for r in rows),
        "challenges_refuted": sum(len(r["refuted"]) for r in rows),
        "clean_briefs": len(clean), "clean_false_vetoes": sum(1 for r in clean if r["confirmed"]),
        "median_seconds": sorted(r["seconds"] for r in answered)[len(answered) // 2] if answered else None,
    }
    (OUT / "llm_critic_report.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1),
                                                encoding="utf-8")
    by_fault: dict[str, list[int]] = {}
    for r in answered:
        by_fault.setdefault(r["fault"], [0, 0])
        by_fault[r["fault"]][0] += 1
        by_fault[r["fault"]][1] += bool(r["confirmed"])
    lines = [
        "# LLM critic on its own (Nemotron through Nightshift)", "",
        f"Generated {summary['generated_at']} by `uv run task eval-llm`. Model: {', '.join(models) or 'none'}.", "",
        "The four golden briefs, each corrupted with every seeded fault that applies, go to the LLM critic with the "
        "deterministic gates bypassed. A fault counts as caught when the model raises a checkable challenge that "
        "code confirms against the evidence. In the product the deterministic gates run first; this measures what "
        "the model adds on its own.", "",
        f"- Answered: {summary['answered']} of {summary['seeded']} faulty briefs (median {summary['median_seconds']} s).",
        f"- Caught by a challenge code confirmed: **{summary['caught_by_confirmed_challenge']} of {summary['answered']}**.",
        f"- Flagged in free text (notes, not checkable): {summary['flagged_in_free_text']} of {summary['answered']}.",
        f"- Challenges raised: {summary['challenges_raised']}; refuted by code: {summary['challenges_refuted']}.",
        f"- Clean briefs: {summary['clean_briefs']}; vetoed by a confirmed challenge: {summary['clean_false_vetoes']}.", "",
        "| Fault | Answered | Caught by a confirmed challenge |", "| --- | --- | --- |",
        *[f"| {k} | {v[0]} | {v[1]} |" for k, v in by_fault.items()],
    ]
    (OUT / "llm_critic_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
