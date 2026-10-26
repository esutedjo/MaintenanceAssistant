"""What a signed decision ran on, stamped on each decision-log entry when it is stored: the code version (the git
commit, "+dirty" when tracked files have uncommitted changes, "unknown" without git), the asset pack version, the
SHA-256 of every model artifact the agents load, and the LLM that answered (a live model id, "recorded <model>" for a
sample-mode replay, or "none")."""

from __future__ import annotations

import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from casefile.schemas import CaseFile
from settings import ROOT

# Every file the RUL methods, the fault-mode matcher and the anomaly flag read at run time (tools/model_tools.py).
MODEL_FILES = ("rul_a.json", "rul_a_p10.txt", "rul_a_p50.txt", "rul_a_p90.txt", "rul_b.json", "fault_mode.json",
               "fleet_curve.json")


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def code_version() -> str:
    commit = _git("rev-parse", "HEAD")
    if not commit:
        return "unknown"
    return f"{commit}+dirty" if _git("status", "--porcelain", "--untracked-files=no") else commit


@lru_cache(maxsize=16)
def _sha256(path: str, mtime_ns: int, size: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_hashes(pack: dict) -> dict[str, str | None]:
    directory = ROOT / pack["models"]["directory"]
    out: dict[str, str | None] = {}
    for name in MODEL_FILES:
        path = directory / name
        stat = path.stat() if path.exists() else None
        out[name] = _sha256(str(path), stat.st_mtime_ns, stat.st_size) if stat else None
    return out


def llm_used(case: CaseFile) -> str:
    """The model behind the intake reading and the critic's review: live ids, "recorded ..." or "none"."""
    calls = [(v.model, v.recorded) for v in case.verdicts if v.gate == "critic" and v.model]
    if case.intent and case.intent.resolved_by:
        calls.insert(0, (case.intent.resolved_by, case.intent.recorded))
    if not calls:
        return "none"
    live = [model for model, recorded in calls if not recorded]
    if live:
        return ", ".join(dict.fromkeys(live))
    return "recorded " + ", ".join(dict.fromkeys(model for model, _ in calls))


def versions(case: CaseFile, pack: dict) -> dict[str, Any]:
    pack_version = case.asset.pack_version if case.asset else pack["version"]
    return {"code": code_version(), "asset_pack": f"{pack['asset_type']}@{pack_version}",
            "models": model_hashes(pack), "llm": llm_used(case)}
