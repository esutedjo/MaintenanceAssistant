"""Offline evaluation in sample mode. Runs the full agent graph on the active fleet, scores the briefs against the
NASA answer key, and checks that the deterministic gates catch seeded faulty briefs.

The answer key (true RUL, proxy fault mode) is read from the raw C-MAPSS files here and nowhere else; agents never
see it. Run `uv run task download` first on a clean clone.

    uv run task eval                 # all 200 active engines, writes evaluation/report.json and report.md
                                     # (plus runs.csv per engine, git-ignored because it holds the answer key)
    uv run task eval --limit 20      # quick check
"""

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from agents.critic import deterministic_gaps
from assurance.rules import run_rule_checks
from casefile.schemas import CaseFile
from data.db import load_pack
from data.fleet import RAW_DIR, build_fleet
from evaluation.faults import FAULTS
from graph.runner import execute_run
from settings import ROOT, Settings

OUT_DIR = Path(__file__).resolve().parent
GOLDEN = {"FD001-A001": "healthy", "FD001-A031": "hpc_parts_short", "FD003-A100": "fan_two_rounds",
          "FD001-A070": "methods_disagree"}
QUESTION = "What is wrong with {asset}, how long do I have, and what should I do?"
IDENTIFIER = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*-[\w.-]*|\b\d{2}-\d{2}-\d{2}\b|\b\d+\.[A-Z]\b|\b[A-Z]{2}-[A-Z]{2,3}-\d+")
NUMBER = re.compile(r"\d")
COMPUTED = {"model", "query", "rule", "case"}


def has_quantity(text: str) -> bool:
    """A number in the claim that is not part of a document, task, part or evidence identifier."""
    return bool(NUMBER.search(IDENTIFIER.sub(" ", text)))


def answer_key(cycles_per_day: int) -> pd.DataFrame:
    if not (RAW_DIR / "RUL_FD001.txt").exists():
        raise SystemExit(f"answer key not found in {RAW_DIR}; run: uv run task download")
    assets, _ = build_fleet(cycles_per_day)
    active = assets[assets["fleet_status"] == "active"]
    return active.set_index("asset_id")[["dataset", "true_rul_at_last_cycle", "proxy_fault_mode"]]


def _lower_bound(case: CaseFile) -> float | None:
    a, b = case.findings.rul_ml, case.findings.rul_trend
    if a is None:
        return None
    return min(a.lower, b.lower if b else a.lower)


def _claims(case: CaseFile):
    return [*case.findings.claims, *(case.recommendation.rationale if case.recommendation else [])]


def run_fleet(assets: list[str], settings: Settings) -> tuple[list[dict], dict[str, CaseFile]]:
    rows, cases = [], {}
    for n, asset in enumerate(assets, start=1):
        start = time.monotonic()
        try:
            outcome = execute_run(QUESTION.format(asset=asset), settings, run_id=f"eval-{asset}")
            case, error = outcome.case, None
        except Exception as exc:                       # a crash is a completion failure, not a harness failure
            case, error = None, f"{type(exc).__name__}: {exc}"
        duration = time.monotonic() - start
        (ROOT / "logs" / f"trace-eval-{asset}.jsonl").unlink(missing_ok=True)
        row = {"asset_id": asset, "status": case.status if case else "crash", "duration_s": round(duration, 2),
               "error": error}
        if case is not None:
            cases[asset] = case
            a, b = case.findings.rul_ml, case.findings.rul_trend
            claims = _claims(case)
            numeric = [c for c in claims if has_quantity(c.text)]
            row.update({
                "rul_a": a.rul if a else None, "a_lower": a.lower if a else None, "a_upper": a.upper if a else None,
                "rul_b": b.rul if b else None, "b_at_cap": b.at_cap if b else None,
                "lower_bound": _lower_bound(case),
                "fault_mode": case.findings.fault_modes[0].failure_mode if case.findings.fault_modes else None,
                "fault_p": case.findings.fault_modes[0].probability if case.findings.fault_modes else None,
                "escalations": [e.rule for e in case.escalations],
                "rounds": max((v.round for v in case.verdicts if v.gate == "critic"), default=0),
                "action": case.recommendation.action if case.recommendation else None,
                "confidence": case.recommendation.confidence if case.recommendation else None,
                "claims": len(claims),
                "claims_resolved": sum(1 for c in claims if c.evidence_ids and not case.unresolved(c.evidence_ids)),
                "numeric_claims": len(numeric),
                "numeric_claims_computed": sum(
                    1 for c in numeric
                    if any(case.evidence[i].kind in COMPUTED for i in c.evidence_ids if i in case.evidence)),
                "numeric_claims_document_only": sum(
                    1 for c in numeric
                    if c.evidence_ids and all(case.evidence[i].kind == "document" for i in c.evidence_ids
                                              if i in case.evidence)),
                "degraded": list(case.degraded),
            })
        rows.append(row)
        if n % 20 == 0 or n == len(assets):
            print(f"  {n}/{len(assets)} runs", flush=True)
    return rows, cases


def _rmse(error: pd.Series) -> float:
    return round(float(np.sqrt((error ** 2).mean())), 2)


def fleet_metrics(frame: pd.DataFrame, cap: float, limit: float) -> dict:
    done = frame[frame["status"] != "crash"]
    scored = done.dropna(subset=["rul_a"])
    true = scored["true_rul_at_last_cycle"].clip(upper=cap)
    a_err, b_err = scored["rul_a"] - true, scored["rul_b"] - true
    lower = scored["lower_bound"]
    unsafe = scored[lower > scored["true_rul_at_last_cycle"]]
    flagged = scored[(scored["rul_a"] - scored["rul_b"]).abs() > limit]
    unflagged = scored.drop(flagged.index)
    near = scored[scored["true_rul_at_last_cycle"] <= 25]
    fd003 = scored[(scored["dataset"] == "FD003") & scored["fault_mode"].notna()]
    fd003_worn = fd003[fd003["true_rul_at_last_cycle"] <= 60]
    durations = frame["duration_s"]
    return {
        "runs": len(frame),
        "completion": {
            "by_status": frame["status"].value_counts().to_dict(),
            "brief_ready_share": round(float((frame["status"] == "needs_review").mean()), 3),
            "crashes": int((frame["status"] == "crash").sum()),
            "duration_s": {"p50": round(float(durations.median()), 2), "p95": round(float(durations.quantile(0.95)), 2),
                           "max": round(float(durations.max()), 2)},
        },
        "rul": {
            "a": {"rmse": _rmse(a_err), "mae": round(float(a_err.abs().mean()), 2),
                  "interval_coverage": round(float(((true >= scored["a_lower"]) & (true <= scored["a_upper"])).mean()), 3)},
            "b": {"rmse": _rmse(b_err), "mae": round(float(b_err.abs().mean()), 2),
                  "rmse_true_rul_le_25": _rmse((near["rul_b"] - near["true_rul_at_last_cycle"].clip(upper=cap)))},
        },
        "safety": {
            "planning_bound_at_or_below_true_rul_share": round(float((lower <= scored["true_rul_at_last_cycle"]).mean()), 3),
            "bound_above_true_rul": len(unsafe),
            "of_which_flagged_for_inspection_or_low_confidence": int(
                ((unsafe["confidence"] == "low") | unsafe["escalations"].map(lambda e: "rul_divergence" in e)).sum()),
            "mean_overshoot_cycles": round(float((unsafe["lower_bound"] - unsafe["true_rul_at_last_cycle"]).mean()), 1)
            if len(unsafe) else 0.0,
            "overshoot_with_true_rul_le_30": int((unsafe["true_rul_at_last_cycle"] <= 30).sum()),
            "engines_with_true_rul_le_30": int((scored["true_rul_at_last_cycle"] <= 30).sum()),
            "overshoot_engines": {r.asset_id: {"bound": round(float(r.lower_bound), 1), "true_rul": int(r.true_rul_at_last_cycle)}
                                  for r in unsafe.itertuples()},
        },
        "disagreement": {
            "flagged": len(flagged), "flagged_share": round(len(flagged) / max(len(scored), 1), 3),
            "a_mae_flagged": round(float((flagged["rul_a"] - flagged["true_rul_at_last_cycle"].clip(upper=cap)).abs().mean()), 2)
            if len(flagged) else None,
            "a_mae_not_flagged": round(float((unflagged["rul_a"] - unflagged["true_rul_at_last_cycle"].clip(upper=cap)).abs().mean()), 2),
        },
        "fault_mode": {
            "fd003_engines": len(fd003),
            "fd003_accuracy_vs_proxy": round(float((fd003["fault_mode"] == fd003["proxy_fault_mode"]).mean()), 3)
            if len(fd003) else None,
            "fd003_accuracy_true_rul_le_60": round(float((fd003_worn["fault_mode"] == fd003_worn["proxy_fault_mode"]).mean()), 3)
            if len(fd003_worn) else None,
            "fd003_engines_true_rul_le_60": len(fd003_worn),
        },
        "evidence": {
            "claims": int(done["claims"].sum()),
            "claims_with_resolvable_evidence_share": round(float(done["claims_resolved"].sum() / max(done["claims"].sum(), 1)), 4),
            "numeric_claims": int(done["numeric_claims"].sum()),
            "numeric_claims_citing_computed_evidence_share": round(
                float(done["numeric_claims_computed"].sum() / max(done["numeric_claims"].sum(), 1)), 4),
            "numeric_claims_quoting_documents_only": int(done["numeric_claims_document_only"].sum()),
        },
        "critic": {
            "rounds": done["rounds"].value_counts().sort_index().to_dict(),
            "escalations": pd.Series([e for es in done["escalations"] for e in es]).value_counts().to_dict(),
        },
    }


def _gates(case: CaseFile, pack: dict, settings: Settings) -> list[str]:
    return [*run_rule_checks(case, pack, settings.reference_time.date()).gaps, *deterministic_gaps(case, pack)]


def fault_metrics(cases: dict[str, CaseFile], pack: dict, settings: Settings, sample: int) -> dict:
    ready = [a for a, c in cases.items() if c.status == "needs_review" and c.recommendation is not None]
    rng = np.random.default_rng(20261023)
    others = [a for a in ready if a not in GOLDEN]
    chosen = [a for a in GOLDEN if a in ready] + list(rng.choice(others, min(sample, len(others)), replace=False))
    false_alarms = [a for a in chosen if _gates(cases[a], pack, settings)]
    by_fault: dict[str, dict] = {}
    misses = []
    for name, fault in FAULTS.items():
        seeded = caught = 0
        for asset in chosen:
            faulty = fault(cases[asset], pack)
            if faulty is None:
                continue
            seeded += 1
            if _gates(faulty, pack, settings):
                caught += 1
            else:
                misses.append(f"{name} on {asset}")
        by_fault[name] = {"seeded": seeded, "caught": caught}
    seeded = sum(v["seeded"] for v in by_fault.values())
    caught = sum(v["caught"] for v in by_fault.values())
    return {"briefs": len(chosen), "clean_briefs_flagged": false_alarms, "seeded": seeded, "caught": caught,
            "catch_rate": round(caught / max(seeded, 1), 3), "by_fault": by_fault, "missed": misses[:40]}


def markdown(report: dict) -> str:
    f, s = report["fleet"], report["seeded_faults"]
    c, r, sa, d, fm, ev = f["completion"], f["rul"], f["safety"], f["disagreement"], f["fault_mode"], f["evidence"]
    lines = [
        "# Evaluation report (sample mode, synthetic fleet)", "",
        f"Generated {report['generated_at']} by `uv run task eval`. {f['runs']} active engines through the full agent "
        "graph on the committed snapshot; scored against the NASA C-MAPSS answer key, which the agents never read.", "",
        "## Run completion", "",
        f"- Statuses: {', '.join(f'{k} {v}' for k, v in c['by_status'].items())}; crashes {c['crashes']}.",
        f"- Time per run: median {c['duration_s']['p50']} s, 95th percentile {c['duration_s']['p95']} s, "
        f"max {c['duration_s']['max']} s (limit 120 s).", "",
        "## Remaining life", "",
        f"- RUL A (LightGBM): RMSE {r['a']['rmse']}, MAE {r['a']['mae']}; 10th-90th interval covers "
        f"{r['a']['interval_coverage']:.0%} of true values (nominal 80%).",
        f"- RUL B (health index): RMSE {r['b']['rmse']}, MAE {r['b']['mae']}; RMSE {r['b']['rmse_true_rul_le_25']} "
        "when true RUL is 25 cycles or less.",
        f"- Planning bound (min of A 10th percentile and B lower) is at or below the true RUL for "
        f"{sa['planning_bound_at_or_below_true_rul_share']:.1%} of engines. {sa['bound_above_true_rul']} engines overshoot "
        f"(mean {sa['mean_overshoot_cycles']} cycles); flagged for inspection or low confidence among them: "
        f"{sa['of_which_flagged_for_inspection_or_low_confidence']}. Near failure it matters most: of the "
        f"{sa['engines_with_true_rul_le_30']} engines within 30 cycles (15 days) of failure, "
        f"{sa['overshoot_with_true_rul_le_30']} overshoot.",
        f"- Methods disagree (> 20 cycles) on {d['flagged']} engines ({d['flagged_share']:.0%}); RUL A error there is "
        f"{d['a_mae_flagged']} vs {d['a_mae_not_flagged']} elsewhere, so the flag points at genuinely uncertain engines.", "",
        "## Fault mode", "",
        f"- FD003 accuracy against the proxy label: {fm['fd003_accuracy_vs_proxy']:.1%} of {fm['fd003_engines']} engines; "
        f"{fm['fd003_accuracy_true_rul_le_60']:.1%} of the {fm['fd003_engines_true_rul_le_60']} engines within 60 cycles "
        "of failure (early in life the signature has not separated yet).", "",
        "## Evidence", "",
        f"- {ev['claims']} claims; {ev['claims_with_resolvable_evidence_share']:.1%} cite evidence IDs that resolve.",
        f"- {ev['numeric_claims']} claims state a quantity (identifiers such as document sections excluded); "
        f"{ev['numeric_claims_citing_computed_evidence_share']:.1%} cite model, query, rule or case-memory evidence "
        f"computed in code, and {ev['numeric_claims_quoting_documents_only']} quote a limit from a document only.", "",
        "## Critic and escalation", "",
        f"- Critic rounds per run: {', '.join(f'{k}: {v}' for k, v in f['critic']['rounds'].items())}.",
        f"- Escalations: {', '.join(f'{k} {v}' for k, v in f['critic']['escalations'].items())}.", "",
        "## Seeded faulty briefs", "",
        f"{s['seeded']} faulty briefs built from {s['briefs']} finished briefs (the 4 golden cases plus a random sample). "
        f"The deterministic gates (rule checks, then the critic's deterministic checks; no LLM) caught "
        f"**{s['caught']} of {s['seeded']} ({s['catch_rate']:.0%})**. Clean briefs flagged: {len(s['clean_briefs_flagged'])}.", "",
        "| Fault | Seeded | Caught |", "| --- | --- | --- |",
        *[f"| {k} | {v['seeded']} | {v['caught']} |" for k, v in s["by_fault"].items()],
    ]
    if s["missed"]:
        lines += ["", "Missed: " + "; ".join(s["missed"]) + "."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N active engines")
    parser.add_argument("--fault-sample", type=int, default=26, help="extra briefs (besides golden) to corrupt")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    pack = load_pack()
    cap, limit = float(pack["thresholds"]["rul_cap_cycles"]), pack["thresholds"]["rul_divergence_cycles"]
    truth = answer_key(pack["thresholds"]["cycles_per_day"])
    assets = sorted(truth.index)
    if args.limit:
        assets = sorted(set(assets[: args.limit]) | (set(GOLDEN) & set(truth.index)))
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(_env_file=None, sample_mode=True, context_sqlite_path=Path(tmp) / "context.sqlite")
        print(f"running {len(assets)} engines in sample mode")
        rows, cases = run_fleet(assets, settings)
        frame = pd.DataFrame(rows).set_index("asset_id").join(truth)
        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "settings": {"sample_mode": True, "engines": len(assets), "rul_cap": cap, "divergence_limit": limit},
            "fleet": fleet_metrics(frame.reset_index(), cap, limit),
            "seeded_faults": fault_metrics(cases, pack, settings, args.fault_sample),
        }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (args.out / "report.md").write_text(markdown(report), encoding="utf-8")
    frame.reset_index().to_csv(args.out / "runs.csv", index=False)
    print(markdown(report))
    return 0 if report["fleet"]["completion"]["crashes"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
