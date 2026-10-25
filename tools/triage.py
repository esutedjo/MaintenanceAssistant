"""Fleet triage: run the full agent graph on every active engine (sample mode, committed snapshot) and rank them by
deadline. Answers "which engines need attention this week?". Run: uv run task fleet-triage"""

from __future__ import annotations

import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from settings import ROOT, Settings
from tools.data_access import SNAPSHOT_DIR

TRIAGE_PATH = SNAPSHOT_DIR / "fleet_triage.parquet"
QUESTION = "What is wrong with {asset}, how long do I have, and what should I do?"
DECISION_ORDER = {"remove, swap or repair": 0, "inspect": 1, "keep flying": 2, "no recommendation": 3}


def decision_kind(action: str | None) -> str:
    text = (action or "").lower()
    if not text:
        return "no recommendation"
    if text.startswith("continue"):
        return "keep flying"
    if "inspect" in text or "borescope" in text:
        return "inspect"
    return "remove, swap or repair"


def triage_row(case, reference) -> dict:
    rec = case.recommendation
    a, b = case.findings.rul_ml, case.findings.rul_trend
    deadline = rec.deadline if rec else None
    return {
        "asset_id": case.asset.asset_id if case.asset else None,
        "dataset": case.asset.variant if case.asset else None,
        "decision": decision_kind(rec.action if rec else None),
        "action": rec.action if rec else None,
        "deadline": deadline.isoformat() if deadline else None,
        "days_to_deadline": (deadline - reference).days if deadline else None,
        "lower_bound_cycles": round(min(a.lower, b.lower if b else a.lower), 1) if a else None,
        "rul_a": round(a.rul, 1) if a else None,
        "rul_b": round(b.rul, 1) if b else None,
        "fault_mode": case.findings.fault_modes[0].failure_mode if case.findings.fault_modes else None,
        "escalations": ", ".join(e.rule.replace("_", " ") for e in case.escalations),
        "confidence": rec.confidence if rec else None,
        "status": case.status,
    }


def build_triage(assets: list[str] | None = None, progress=None) -> pd.DataFrame:
    from graph.runner import execute_run
    from ui.fleet import list_active_assets

    assets = assets or list_active_assets()["asset_id"].tolist()
    rows = []
    logging.disable(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, sample_mode=True, context_sqlite_path=Path(tmp) / "context.sqlite")
            reference = settings.reference_time.date()
            for n, asset in enumerate(assets, start=1):
                outcome = execute_run(QUESTION.format(asset=asset), settings, run_id=f"triage-{asset}")
                (ROOT / "logs" / f"trace-triage-{asset}.jsonl").unlink(missing_ok=True)
                rows.append(triage_row(outcome.case, reference))
                if progress:
                    progress(n, len(assets))
    finally:
        logging.disable(logging.NOTSET)
    frame = pd.DataFrame(rows)
    frame["order"] = frame["decision"].map(DECISION_ORDER)
    frame = frame.sort_values(["order", "days_to_deadline", "lower_bound_cycles"]).drop(columns="order")
    frame.attrs["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return frame.reset_index(drop=True)


def save_triage(frame: pd.DataFrame, path: Path = TRIAGE_PATH) -> None:
    frame.to_parquet(path, index=False)
    path.with_suffix(".txt").write_text(frame.attrs.get("generated_at", ""), encoding="utf-8")


def load_triage(path: Path = TRIAGE_PATH) -> pd.DataFrame | None:
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    stamp = path.with_suffix(".txt")
    frame.attrs["generated_at"] = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""
    return frame


def main() -> int:
    start = time.monotonic()
    frame = build_triage(progress=lambda n, total: print(f"  {n}/{total}", flush=True) if n % 50 == 0 else None)
    save_triage(frame)
    counts = frame["decision"].value_counts().to_dict()
    print(f"fleet triage: {len(frame)} engines in {time.monotonic() - start:.0f} s -> {TRIAGE_PATH.name}; {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
