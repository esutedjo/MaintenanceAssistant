"""Recorded Nemotron responses for sample mode. `uv run task record-llm` sends the golden briefs' critic prompts (and
one free-text intake prompt) through Nightshift once and stores the reply text, the served model, the prompt hash and
the capture time in data/snapshot/llm_recordings.json; never tokens, headers or settings.

Sample mode replays a recording only when the prompt's SHA-256 matches exactly, so a changed brief or prompt gets no
reply (deterministic checks only, as before). Code checks a replayed reply exactly as a live one."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from settings import ROOT

RECORDINGS = ROOT / "data" / "snapshot" / "llm_recordings.json"
# Set by tools/record_llm.py while capturing: (purpose, prompt) -> Recording from a live Nightshift call, and the
# set of recordings the capture runs hit (kept; the rest are stale and dropped).
capture: Callable[[str, str], "Recording | None"] | None = None
used: set[str] | None = None


class Recording(BaseModel):
    purpose: str                    # "critic" or "intake"
    case: str | None = None         # the golden question it was captured for
    prompt_sha256: str
    model: str                      # model Nightshift reported serving the reply
    captured_at: str                # UTC, ISO 8601
    text: str

    @property
    def label(self) -> str:
        family = "Nemotron" if "nemotron" in self.model.lower() else self.model
        return f"recorded {family} response (captured {self.captured_at[:10]})"


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def load(path: Path | None = None) -> dict[str, Recording]:
    path = path or RECORDINGS
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8")).get("recordings") or []
    return {r["prompt_sha256"]: Recording(**r) for r in rows}


def lookup(prompt: str, purpose: str, path: Path | None = None) -> Recording | None:
    """The recorded reply to exactly this prompt, or None. While capturing, an unknown prompt goes to Nightshift."""
    found = load(path).get(prompt_hash(prompt))
    if found is not None and found.purpose == purpose:
        if used is not None:
            used.add(found.prompt_sha256)
        return found
    return capture(purpose, prompt) if capture is not None else None


def save(recordings: list[Recording], path: Path | None = None) -> None:
    path = path or RECORDINGS
    body = {"note": ("Nemotron replies captured through Nightshift by `uv run task record-llm` for the golden briefs. "
                     "Sample mode replays one only when the SHA-256 of the prompt matches exactly; code verifies "
                     "every challenge as in live mode. No tokens, headers or settings are stored."),
            "recordings": [r.model_dump() for r in recordings]}
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
