"""Engineer sign-off writes a draft work order. Sample mode (and any run without PG_APP_USER) appends
JSONL under .state/; live mode inserts into cmms.work_orders as origin='agent' and keeps the JSONL record too.
Approval drafts a work order for the organisation's own release process; it is not a release to service.
Both decisions need the signer's name (approve also needs every urgent, warning and mandatory-document escalation
acknowledged and, when one is urgent, a typed reason for approving despite it; info escalations are notes the log
records as seen) and append to the case's hash-chained decision log (.state/decision_logs/<case_id>.jsonl), whose full
entries the work-order record carries. Each stored entry is stamped with the code, asset pack, model and LLM versions;
the brief exactly as shown is stored as .state/briefs/<case_id>-<sha12>.json and its SHA-256 goes in the engineer's
entry; the log's head hash is anchored outside the log, in the case-memory record and the run's trace.
H-MEM is best-effort: the decision is stored as a memory and every cited past case gets feedback (helpful on approve,
not helpful on reject, unless the engineer marks it), which moves its trust and so its rank in later searches."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence

import psycopg
from pydantic import ValidationError

from assurance.escalation import needs_acknowledgement
from casefile import calls as service_calls
from casefile.context_store import FeedbackKind, MemoryIn, MemoryProvenance, open_context_store
from casefile.decision_log import (
    BriefSnapshot, Signer, append_entries, derive_log, log_path, read_log, run_id_of, signoff_entry,
)
from casefile.world import signoff_batch
from casefile.schemas import CaseFile, Escalation, TraceEvent
from casefile.trace import LOG_DIR, TraceWriter
from data.db import load_pack
from settings import ROOT, Settings
from tools.versions import versions

Decision = Literal["approved", "rejected"]
DRAFT_PATH = ROOT / ".state" / "draft_work_orders.jsonl"
RELEASE_NOTE: dict[Decision, str] = {
    "approved": ("Draft work order for the organisation's own release process; not a certificate of release to "
                 "service."),
    "rejected": "Rejected at sign-off; no work order drafted.",
}
# How the head hash is written where the log file cannot reach it: the run's trace and the case-memory record.
HEAD = re.compile(r"decision log head ([0-9a-f]{64})", re.IGNORECASE)


DEFAULT_CASE_FEEDBACK: dict[Decision, FeedbackKind] = {"approved": "helpful", "rejected": "not_helpful"}


def approve_case(case: CaseFile, settings: Settings, comment: str = "", path: Path | None = None,
                 case_feedback: dict[str, FeedbackKind] | None = None, *, signer: Signer | str,
                 acknowledged: Sequence[int] = (), reason: str = "", correction: bool = False) -> dict[str, Any]:
    """`acknowledged` holds the indexes of `case.escalations` the engineer ticked as read; approve needs every
    urgent, warning and mandatory-document one, and with an urgent escalation also a typed `reason` for approving
    despite it. Info escalations need no tick: the entry records them as noted. A case already signed is refused
    unless `correction` is set (the new entry then says it corrects the earlier one)."""
    if case.status != "needs_review":
        raise ValueError(f"cannot approve a case in status {case.status!r}")
    if case.recommendation is None:
        raise ValueError("cannot approve a case with no recommendation")
    signer, path = _signer(signer), path or DRAFT_PATH
    unread = [e.rule for i, e in enumerate(case.escalations)
              if needs_acknowledgement(e.severity, e.rule) and i not in set(acknowledged)]
    if unread:
        raise ValueError(f"approve needs every urgent, warning and mandatory escalation acknowledged; "
                         f"not acknowledged: {', '.join(unread)}")
    urgent = [e.rule for e in case.escalations if e.severity == "urgent"]
    if urgent and not (reason or "").strip():
        raise ValueError(f"approve despite an urgent escalation needs a reason; urgent: {', '.join(urgent)}")
    with case_lock(path.parent, case.case_id):
        _refuse_second_signoff(case, path.parent, correction)
        record = _record(case, "approved", comment)
        _log_decision(case, record, signer, comment, case_feedback, acknowledged, path.parent, reason, correction)
        _persist_work_order(record, settings, path)
    calls: list = []
    record["memory"] = _remember(settings, case, "approved", comment, case_feedback, record, calls)
    _anchor_trace(case, record, signer, calls)
    return record


def reject_case(case: CaseFile, settings: Settings, comment: str = "", path: Path | None = None,
                case_feedback: dict[str, FeedbackKind] | None = None, *, signer: Signer | str,
                acknowledged: Sequence[int] = (), correction: bool = False) -> dict[str, Any]:
    """Reject needs no acknowledgement; escalations ticked anyway are recorded. A case already signed is refused
    unless `correction` is set."""
    if case.status != "needs_review":
        raise ValueError(f"cannot reject a case in status {case.status!r}")
    signer, path = _signer(signer), path or DRAFT_PATH
    with case_lock(path.parent, case.case_id):
        _refuse_second_signoff(case, path.parent, correction)
        record = _record(case, "rejected", comment)
        _log_decision(case, record, signer, comment, case_feedback, acknowledged, path.parent, "", correction)
        _append_jsonl(path, record)
        record["backend"] = "file"
    calls: list = []
    record["memory"] = _remember(settings, case, "rejected", comment, case_feedback, record, calls)
    _anchor_trace(case, record, signer, calls)
    return record


_CASE_LOCKS: dict[str, threading.Lock] = {}
_CASE_LOCKS_GUARD = threading.Lock()
STALE_LOCK_S = 120.0


@contextmanager
def case_lock(state_dir: Path, case_id: str, timeout_s: float = 15.0) -> Iterator[None]:
    """One sign-off per case at a time: a lock per case in this process plus an O_CREAT|O_EXCL lockfile beside the
    case's decision log for other processes (the UI and the API). A lockfile older than STALE_LOCK_S is a crashed
    writer's and is taken over."""
    with _CASE_LOCKS_GUARD:
        local = _CASE_LOCKS.setdefault(case_id, threading.Lock())
    with local:
        lockfile = log_path(state_dir / "decision_logs", case_id).with_suffix(".lock")
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
                    raise ValueError(f"another sign-off of case {case_id} is in progress; try again") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(handle)
            lockfile.unlink(missing_ok=True)


def _refuse_second_signoff(case: CaseFile, state_dir: Path, correction: bool) -> None:
    """A brief is signed once. A later decision on the same case must be an explicit correction entry."""
    if correction:
        return
    for row in read_log(log_path(state_dir / "decision_logs", case.case_id)):
        if row.get("actor_type") == "human" and row.get("signer"):
            raise ValueError(f"case {case.case_id} is already signed ({row.get('decision')} by "
                             f"{row['signer'].get('name')}); a second decision must be an explicit correction")


def _acknowledged(case: CaseFile, indexes: Sequence[int]) -> list[Escalation]:
    ticked = set(indexes)
    return [esc for i, esc in enumerate(case.escalations) if i in ticked]


def _noted(case: CaseFile, indexes: Sequence[int]) -> list[Escalation]:
    """Info escalations shown as notes at sign-off and not ticked: the log records them as seen."""
    ticked = set(indexes)
    return [esc for i, esc in enumerate(case.escalations)
            if i not in ticked and not needs_acknowledgement(esc.severity, esc.rule)]


def _signer(value: Signer | str) -> Signer:
    """A sign-off needs the signer's name; the UI keeps approve and reject disabled until one is typed."""
    if isinstance(value, Signer):
        return value
    try:
        return Signer(name=value)
    except ValidationError:
        raise ValueError("a sign-off needs the signer's name") from None


def snapshot_brief(case: CaseFile, state_dir: Path) -> BriefSnapshot:
    """Store the brief exactly as shown (the case JSON) as briefs/<case_id>-<sha12>.json beside the decision logs.
    The SHA-256 is of the file's bytes, so the engineer's entry pins what was read, documents used included."""
    data = (json.dumps(case.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    path = state_dir / "briefs" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', case.case_id)}-{sha[:12]}.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return BriefSnapshot(path=_display_path(path), sha256=sha)


def _log_decision(case: CaseFile, record: dict[str, Any], signer: Signer, comment: str,
                  case_feedback: dict[str, FeedbackKind] | None, acknowledged: Sequence[int], state_dir: Path,
                  reason: str = "", correction: bool = False) -> None:
    """Append the derived decisions and the engineer's entry to the case's log, each stamped with the versions the
    case ran on; the record carries all of it."""
    ratings = {cited["evidence_id"]: kind for cited, kind in _ratings(case, record["decision"], case_feedback)}
    snapshot = snapshot_brief(case, state_dir)
    entry = signoff_entry(case, record["decision"], signer, comment, ratings, wo_id=record["wo_id"],
                          acknowledged=_acknowledged(case, acknowledged), reason=reason,
                          noted=_noted(case, acknowledged), snapshot=snapshot)
    if correction:
        entry = entry.model_copy(update={"rationale": f"Correction of the earlier sign-off. {entry.rationale}"})
    stamp = versions(case, load_pack())
    path = log_path(state_dir / "decision_logs", case.case_id)
    rows = append_entries(path, [e.model_copy(update={"versions": stamp}) for e in [*derive_log(case), entry]])
    record.update(signed_by=signer.model_dump(), signed_at=entry.timestamp.isoformat(),
                  brief=entry.brief.model_dump() if entry.brief else None, brief_snapshot=snapshot.model_dump(),
                  versions=stamp,
                  decision_log={"path": _display_path(path), "head_hash": rows[-1]["hash"], "entries": rows})


def _anchor_trace(case: CaseFile, record: dict[str, Any], signer: Signer, calls: list | None = None) -> None:
    """The sign-off and the log's head hash as the last line of the run's trace (logs/trace-<run_id>.jsonl), after
    one line per World Model or H-MEM call the sign-off made (asset facts, decision memory, past-case feedback)."""
    head = record["decision_log"]["head_hash"]
    run_id = run_id_of(case)
    try:
        writer = TraceWriter(run_id)
        for event in service_calls.events(run_id, "engineer", calls or []):
            writer.write(event)
        writer.write(TraceEvent(run_id=run_id, agent_name="engineer", action="sign_off",
                                input_summary=f"case={case.case_id}",
                                output_summary=(f"{record['decision']} by {signer.name}; work order {record['wo_id']}; "
                                                f"decision log head {head}")))
        record["anchors"] = {**record.get("anchors", {}), "trace": _display_path(writer.path)}
    except OSError as error:
        record["anchors"] = {**record.get("anchors", {}), "trace_error": type(error).__name__}


def signoff_anchors(case_id: str, run_id: str, settings: Settings, trace_dir: Path = LOG_DIR
                    ) -> dict[str, str | None]:
    """The head hash each anchor recorded at sign-off (None when that anchor is missing): the last sign-off line in
    the run's trace and the case-memory record of the decision."""
    anchors: dict[str, str | None] = {"trace": None, "case memory": None}
    trace = trace_dir / f"trace-{run_id}.jsonl"
    if trace.exists():
        for line in reversed(trace.read_text(encoding="utf-8").splitlines()):
            event = json.loads(line) if line.strip() else {}
            found = HEAD.search(event.get("output_summary") or "") if event.get("action") == "sign_off" else None
            if found:
                anchors["trace"] = found.group(1)
                break
    try:
        store = open_context_store(settings)
    except Exception:
        return anchors
    try:
        for hit in store.search_memory(case_id, top_n=20, tags=["decision", case_id], threshold=0.0):
            found = HEAD.search(hit.content) if hit.source.startswith(f"run:{case_id}:") else None
            if found:
                anchors["case memory"] = found.group(1)
                break
    except Exception:
        pass
    finally:
        store.close()
    return anchors


def cited_cases(case: CaseFile) -> list[dict[str, Any]]:
    """Similar past cases the brief cited: evidence ID, memory ID, summary, trust at run time."""
    out = []
    for evidence_id in case.findings.similar_cases:
        evidence = case.evidence.get(evidence_id)
        if evidence and evidence.content.get("memory_id"):
            out.append({"evidence_id": evidence_id, "memory_id": evidence.content["memory_id"],
                        "summary": evidence.summary, "trust": evidence.content.get("trust")})
    return out


def _ratings(case: CaseFile, decision: Decision, case_feedback: dict[str, FeedbackKind] | None
             ) -> list[tuple[dict[str, Any], FeedbackKind]]:
    """Each cited past case with its rating: the engineer's mark, else the default for the decision."""
    return [(cited, (case_feedback or {}).get(cited["memory_id"], DEFAULT_CASE_FEEDBACK[decision]))
            for cited in cited_cases(case)]


def _record(case: CaseFile, decision: Decision, comment: str) -> dict[str, Any]:
    rec = case.recommendation
    asset_id = case.asset.asset_id if case.asset else "unknown"
    action = rec.action if rec else ""
    return {
        "wo_id": f"WO-DRAFT-{case.case_id}",           # the full case ID: a 6-hex suffix collides at scale
        "asset_id": asset_id,
        # "Swap in lease engine…" or "Do not dispatch; swap in lease engine…"
        "wo_type": "engine_swap" if re.search(r"\bswap in\b", action.lower()) else "scheduled_maintenance",
        "status": "draft" if decision == "approved" else "rejected",
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
        "cycle_at": _last_cycle(case),
        "component": _component(case),
        "description": action or "engineer decision",
        "origin": "agent",
        "case_id": case.case_id,
        "decision": decision,
        "release_note": RELEASE_NOTE[decision],
        "comment": comment or None,
        "parts": [p.model_dump() for p in (rec.parts if rec else [])],
        "slot": rec.slot.isoformat() if rec and rec.slot else None,
        "deadline": rec.deadline.isoformat() if rec and rec.deadline else None,
    }


def _last_cycle(case: CaseFile) -> int:
    if case.findings.anomaly is not None:
        return case.findings.anomaly.cycle
    for evidence in case.evidence.values():
        value = evidence.content.get("last_cycle")
        if value is not None:
            return int(value)
    return 0


def _component(case: CaseFile) -> str:
    modes = case.findings.fault_modes
    if not modes:
        return "engine"
    top = max(modes, key=lambda m: m.probability)
    pack = load_pack()
    return pack.get("failure_modes", {}).get(top.failure_mode, {}).get("component", "engine")


def _persist_work_order(record: dict[str, Any], settings: Settings, path: Path) -> None:
    """The CMMS row keeps its columns; the JSONL record (always written) is where the decision log travels."""
    record["backend"] = "file"
    if not settings.sample_mode and settings.pg_app_user:
        try:
            _insert_cmms(settings, record)
            record["backend"] = "cmms"
        except Exception as error:
            record["cmms_error"] = type(error).__name__
    _append_jsonl(path, record)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    record["path"] = _display_path(path)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _insert_cmms(settings: Settings, record: dict[str, Any]) -> None:
    with psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_db_cmms,
        user=settings.pg_app_user,
        password=settings.pg_app_password.get_secret_value(),
        sslmode=settings.pg_sslmode,
        connect_timeout=15,
    ) as conn:
        conn.execute(
            "INSERT INTO work_orders (wo_id, asset_id, wo_type, status, opened_at, closed_at, cycle_at, "
            "component, description, origin) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s)",
            (record["wo_id"], record["asset_id"], record["wo_type"], record["status"], record["opened_at"],
             record["cycle_at"], record["component"], record["description"], record["origin"]),
        )
        conn.commit()


def _remember(settings: Settings, case: CaseFile, decision: Decision, comment: str,
              case_feedback: dict[str, FeedbackKind] | None = None, record: dict[str, Any] | None = None,
              calls: list | None = None) -> dict[str, Any]:
    """The decision as a case-memory record, which also anchors the decision log's head hash outside the log. Each
    context-store call is added to `calls` for the run's trace."""
    with service_calls.collecting() as made:
        try:
            return _remember_calls(settings, case, decision, comment, case_feedback, record)
        finally:
            if calls is not None:
                calls.extend(made)


def _remember_calls(settings: Settings, case: CaseFile, decision: Decision, comment: str,
                    case_feedback: dict[str, FeedbackKind] | None, record: dict[str, Any] | None) -> dict[str, Any]:
    asset_id = case.asset.asset_id if case.asset else "unknown"
    action = case.recommendation.action if case.recommendation else ""
    note = f"Engineer {decision} {asset_id}: {action}".strip()
    if comment:
        note = f"{note}. {comment}"
    head = ((record or {}).get("decision_log") or {}).get("head_hash")
    if head:
        note = f"{note.rstrip('.')}. Decision log head {head}."
    store = service_calls.TracedStore(open_context_store(settings))
    try:
        world = _write_asset_facts(store, case, decision)
        result = store.write_memories([
            MemoryIn(
                content=note,
                source=f"run:{case.case_id}:{decision}",
                tags=["decision", decision, asset_id, case.case_id],
                confidence=0.9 if decision == "approved" else 0.4,
                provenance=MemoryProvenance(origin="plan_completion", label="engineer sign-off",
                                            refs=[case.case_id, *([f"decision_log:{head}"] if head else [])]),
                goalRunId=case.case_id,
            )
        ])
        if head and record is not None:
            record["anchors"] = {**record.get("anchors", {}),
                                 "case memory": f"{store.backend}:run:{case.case_id}:{decision}"}
        feedback = []
        for cited, kind in _ratings(case, decision, case_feedback):
            try:
                trust = store.feedback(cited["memory_id"], kind)
                feedback.append({**cited, "kind": kind, "trust_after": round(trust, 3)})
            except Exception as error:
                feedback.append({**cited, "kind": kind, "error": type(error).__name__})
        return {"ok": True, "backend": store.backend, "result": result, "case_feedback": feedback, "world": world}
    except Exception as error:
        return {"ok": False, "error": type(error).__name__}
    finally:
        store.close()


def _write_asset_facts(store, case: CaseFile, decision: Decision) -> dict[str, Any]:
    """Best-effort: last sign-off becomes facts on the asset node."""
    try:
        pack = load_pack()
        batch = signoff_batch(pack, case, decision)
        try:
            return {"ok": True, "result": store.write_world(batch)}
        except ValueError:
            batch.relations = []
            return {"ok": True, "result": store.write_world(batch)}
    except Exception as error:
        return {"ok": False, "error": type(error).__name__}
