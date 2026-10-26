"""Engineer sign-off writes a draft work order. Sample mode (and any run without PG_APP_USER) appends
JSONL under .state/; live mode inserts into cmms.work_orders as origin='agent' and keeps the JSONL record too.
Approval drafts a work order for the organisation's own release process; it is not a release to service.
Both decisions need the signer's name (approve also needs every escalation acknowledged and, when one is urgent, a
typed reason for approving despite it) and append to the case's
hash-chained decision log (.state/decision_logs/<case_id>.jsonl), whose full entries the work-order record
carries. H-MEM is best-effort:
the decision is stored as a memory and every cited past case gets feedback (helpful on approve, not helpful on
reject, unless the engineer marks it), which moves its trust and so its rank in later searches."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

import psycopg
from pydantic import ValidationError

from casefile.context_store import FeedbackKind, MemoryIn, MemoryProvenance, open_context_store
from casefile.decision_log import Signer, append_entries, derive_log, log_path, signoff_entry
from casefile.world import signoff_batch
from casefile.schemas import CaseFile, Escalation
from data.db import load_pack
from settings import ROOT, Settings

Decision = Literal["approved", "rejected"]
DRAFT_PATH = ROOT / ".state" / "draft_work_orders.jsonl"
RELEASE_NOTE: dict[Decision, str] = {
    "approved": ("Draft work order for the organisation's own release process; not a certificate of release to "
                 "service."),
    "rejected": "Rejected at sign-off; no work order drafted.",
}


DEFAULT_CASE_FEEDBACK: dict[Decision, FeedbackKind] = {"approved": "helpful", "rejected": "not_helpful"}


def approve_case(case: CaseFile, settings: Settings, comment: str = "", path: Path | None = None,
                 case_feedback: dict[str, FeedbackKind] | None = None, *, signer: Signer | str,
                 acknowledged: Sequence[int] = (), reason: str = "") -> dict[str, Any]:
    """`acknowledged` holds the indexes of `case.escalations` the engineer ticked as read; approve needs all, and
    with an urgent escalation also a typed `reason` for approving despite it."""
    if case.status != "needs_review":
        raise ValueError(f"cannot approve a case in status {case.status!r}")
    if case.recommendation is None:
        raise ValueError("cannot approve a case with no recommendation")
    signer, path, read = _signer(signer), path or DRAFT_PATH, _acknowledged(case, acknowledged)
    if len(read) < len(case.escalations):
        unread = [e.rule for i, e in enumerate(case.escalations) if i not in set(acknowledged)]
        raise ValueError(f"approve needs every escalation acknowledged; not acknowledged: {', '.join(unread)}")
    urgent = [e.rule for e in case.escalations if e.severity == "urgent"]
    if urgent and not (reason or "").strip():
        raise ValueError(f"approve despite an urgent escalation needs a reason; urgent: {', '.join(urgent)}")
    record = _record(case, "approved", comment)
    _log_decision(case, record, signer, comment, case_feedback, read, path.parent / "decision_logs", reason)
    _persist_work_order(record, settings, path)
    record["memory"] = _remember(settings, case, "approved", comment, case_feedback)
    return record


def reject_case(case: CaseFile, settings: Settings, comment: str = "", path: Path | None = None,
                case_feedback: dict[str, FeedbackKind] | None = None, *, signer: Signer | str,
                acknowledged: Sequence[int] = ()) -> dict[str, Any]:
    """Reject needs no acknowledgement; escalations ticked anyway are recorded."""
    if case.status != "needs_review":
        raise ValueError(f"cannot reject a case in status {case.status!r}")
    signer, path, read = _signer(signer), path or DRAFT_PATH, _acknowledged(case, acknowledged)
    record = _record(case, "rejected", comment)
    _log_decision(case, record, signer, comment, case_feedback, read, path.parent / "decision_logs")
    _append_jsonl(path, record)
    record["backend"] = "file"
    record["memory"] = _remember(settings, case, "rejected", comment, case_feedback)
    return record


def _acknowledged(case: CaseFile, indexes: Sequence[int]) -> list[Escalation]:
    ticked = set(indexes)
    return [esc for i, esc in enumerate(case.escalations) if i in ticked]


def _signer(value: Signer | str) -> Signer:
    """A sign-off needs the signer's name; the UI keeps approve and reject disabled until one is typed."""
    if isinstance(value, Signer):
        return value
    try:
        return Signer(name=value)
    except ValidationError:
        raise ValueError("a sign-off needs the signer's name") from None


def _log_decision(case: CaseFile, record: dict[str, Any], signer: Signer, comment: str,
                  case_feedback: dict[str, FeedbackKind] | None, acknowledged: list[Escalation], log_dir: Path,
                  reason: str = "") -> None:
    """Append the derived decisions and the engineer's entry to the case's log; the record carries all of it."""
    ratings = {cited["evidence_id"]: kind for cited, kind in _ratings(case, record["decision"], case_feedback)}
    entry = signoff_entry(case, record["decision"], signer, comment, ratings, wo_id=record["wo_id"],
                          acknowledged=acknowledged, reason=reason)
    path = log_path(log_dir, case.case_id)
    rows = append_entries(path, [*derive_log(case), entry])
    record.update(signed_by=signer.model_dump(), signed_at=entry.timestamp.isoformat(),
                  brief=entry.brief.model_dump() if entry.brief else None,
                  decision_log={"path": _display_path(path), "head_hash": rows[-1]["hash"], "entries": rows})


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
        "wo_id": f"WO-DRAFT-{case.case_id.rsplit('-', 1)[-1]}",
        "asset_id": asset_id,
        "wo_type": "engine_swap" if action.lower().startswith("swap") else "scheduled_maintenance",
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
              case_feedback: dict[str, FeedbackKind] | None = None) -> dict[str, Any]:
    asset_id = case.asset.asset_id if case.asset else "unknown"
    action = case.recommendation.action if case.recommendation else ""
    note = f"Engineer {decision} {asset_id}: {action}".strip()
    if comment:
        note = f"{note}. {comment}"
    store = open_context_store(settings)
    try:
        world = _write_asset_facts(store, case, decision)
        result = store.write_memories([
            MemoryIn(
                content=note,
                source=f"run:{case.case_id}:{decision}",
                tags=["decision", decision, asset_id],
                confidence=0.9 if decision == "approved" else 0.4,
                provenance=MemoryProvenance(origin="plan_completion", label="engineer sign-off",
                                            refs=[case.case_id]),
                goalRunId=case.case_id,
            )
        ])
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
