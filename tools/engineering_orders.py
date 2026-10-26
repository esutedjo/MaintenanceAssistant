"""Engineering orders: engineering's decision to adopt or decline a recommended service bulletin for a whole fleet,
taken once instead of on every brief (the sb_adoption note fires on 134 of 200 engines).

Stored append-only as engineering_orders.jsonl beside the case-memory SQLite file (.state/ by default; a test's or the
eval's temp folder otherwise), with the decision log's conventions: UTC time, a version stamp, and a SHA-256 hash chain
(each line carries its own hash and the previous line's). A later decision for the same fleet and bulletin supersedes
the earlier one, which stays in the file. The knowledge agent reads the current decision for the engine's fleet and
cites it; a file whose chain is broken is ignored and the brief says so. Written from the UI only, not the API."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from casefile.decision_log import GENESIS, Signer, _digest, read_log
from casefile.schemas import FleetDecision, as_utc, utcnow
from settings import Settings

FILE = "engineering_orders.jsonl"
DOC_ID = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")          # SB-TF-72-0142
FLEET = re.compile(r"^[A-Z0-9]{2,12}$")                          # FD001
WORDS = {"adopt": "adopted", "decline": "declined"}
_LOCK = threading.Lock()
STALE_LOCK_S = 120.0


class EngineeringOrder(BaseModel):
    """One signed fleet decision on a recommended service bulletin."""
    eo_id: str
    fleet: str = Field(max_length=12)
    doc_id: str = Field(max_length=40)
    decision: FleetDecision
    signer: Signer
    reference: str | None = Field(None, max_length=120)      # the operator's own EO number or meeting minute
    decided_at: datetime = Field(default_factory=utcnow)
    asset_type: str
    versions: dict[str, Any] | None = None

    @field_validator("decided_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @field_validator("reference", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return (value.strip() or None) if isinstance(value, str) else value


def store_path(settings: Settings) -> Path:
    return Path(settings.context_sqlite_path).parent / FILE


def read_orders(settings: Settings) -> list[dict[str, Any]]:
    return read_log(store_path(settings))


def verify_orders(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """Walk the hash chain: an edited, deleted or reordered line shows."""
    prev = GENESIS
    for n, row in enumerate(rows, 1):
        if row.get("seq") != n or row.get("prev_hash") != prev:
            return False, f"line {n} does not chain to the line before it: a line was deleted or reordered"
        if _digest({k: v for k, v in row.items() if k != "hash"}) != row.get("hash"):
            return False, f"line {n} does not match its hash: it was edited"
        prev = row["hash"]
    return True, f"hash chain intact, {len(rows)} engineering order{'s' if len(rows) != 1 else ''}"


def decided(row: dict[str, Any]) -> str:
    """"declined for fleet FD001 by Jane Doe on 2026-10-26 (EO-FD001-001)"."""
    return (f"{WORDS[row['decision']]} for fleet {row['fleet']} by {row['signer']['name']} on "
            f"{str(row['decided_at'])[:10]} ({row['eo_id']})")


def describe(row: dict[str, Any]) -> str:
    """"SB-TF-72-0142 declined for fleet FD001 by Jane Doe on 2026-10-26 (EO-FD001-001)"."""
    return f"{row['doc_id']} {decided(row)}"


def fleet_decisions(settings: Settings, fleet: str | None) -> tuple[dict[str, dict[str, Any]], str | None]:
    """The current decision per bulletin for one fleet (the latest line wins), and a note when the file cannot be
    trusted (then no decision applies and every brief keeps asking engineering)."""
    path = store_path(settings)
    if not fleet or not path.exists():
        return {}, None
    try:
        rows = read_orders(settings)
    except ValueError as error:                     # json.JSONDecodeError
        return {}, f"engineering orders file unreadable ({type(error).__name__}); fleet decisions ignored"
    intact, why = verify_orders(rows)
    if not intact:
        return {}, f"engineering orders file fails its hash chain ({why}); fleet decisions ignored"
    return {row["doc_id"]: row for row in rows if row.get("fleet") == fleet}, None


@contextmanager
def _locked(path: Path, timeout_s: float = 15.0) -> Iterator[None]:
    """One writer at a time: a lock in this process plus an O_CREAT|O_EXCL lockfile for the UI and other processes."""
    with _LOCK:
        lockfile = path.with_suffix(".lock")
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                handle = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - lockfile.stat().st_mtime > STALE_LOCK_S:
                        lockfile.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise ValueError("another engineering order is being recorded; try again") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(handle)
            lockfile.unlink(missing_ok=True)


def record_order(settings: Settings, *, fleet: str, doc_id: str, decision: FleetDecision, signer: Signer | str,
                 pack: dict, reference: str = "") -> dict[str, Any]:
    """Append one signed fleet decision; returns the stored line. The name is required (typed, not checked against a
    register); the bulletin and the fleet must be ones the asset pack knows."""
    if not FLEET.fullmatch(fleet or "") or fleet not in (pack.get("variants") or {}):
        raise ValueError(f"unknown fleet {fleet!r}")
    if not DOC_ID.fullmatch(doc_id or ""):
        raise ValueError(f"not a document ID: {doc_id!r}")
    if decision not in WORDS:
        raise ValueError("the decision is adopt or decline")
    if not isinstance(signer, Signer):
        if not (signer or "").strip():
            raise ValueError("an engineering order needs the signer's name")
        signer = Signer(name=signer)
    from tools.versions import code_version

    path = store_path(settings)
    with _locked(path):
        rows = read_log(path)
        intact, why = verify_orders(rows)
        if not intact:
            raise ValueError(f"the engineering orders file fails its hash chain ({why}); not recorded")
        stamp = {"code": code_version(), "asset_pack": f"{pack['asset_type']}@{pack['version']}"}
        order = EngineeringOrder(eo_id=f"EO-{fleet}-{len(rows) + 1:03d}", fleet=fleet, doc_id=doc_id,
                                 decision=decision, signer=signer, reference=reference or None,
                                 asset_type=pack["asset_type"], versions=stamp)
        row = {**order.model_dump(mode="json"), "seq": len(rows) + 1,
               "prev_hash": rows[-1]["hash"] if rows else GENESIS}
        row["hash"] = _digest(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    return row
