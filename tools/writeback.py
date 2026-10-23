"""Engineer sign-off writes a draft work order. Sample mode (and any run without PG_APP_USER) appends
JSONL under .state/; live mode inserts into cmms.work_orders as origin='agent'. H-MEM is best-effort: the
decision is stored as a memory and every cited past case gets feedback (helpful on approve, not helpful on
reject, unless the engineer marks it), which moves its trust and so its rank in later searches."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import psycopg

from casefile.context_store import FeedbackKind, MemoryIn, MemoryProvenance, open_context_store
from casefile.schemas import CaseFile
from data.db import load_pack
from settings import ROOT, Settings

Decision = Literal["approved", "rejected"]
DRAFT_PATH = ROOT / ".state" / "draft_work_orders.jsonl"


DEFAULT_CASE_FEEDBACK: dict[Decision, FeedbackKind] = {"approved": "helpful", "rejected": "not_helpful"}


def approve_case(case: CaseFile, settings: Settings, comment: str = "", path: Path | None = None,
                 case_feedback: dict[str, FeedbackKind] | None = None) -> dict[str, Any]:
    if case.status != "needs_review":
        raise ValueError(f"cannot approve a case in status {case.status!r}")
    if case.recommendation is None:
        raise ValueError("cannot approve a case with no recommendation")
    record = _record(case, "approved", comment)
    _persist_work_order(record, settings, path or DRAFT_PATH)
    record["memory"] = _remember(settings, case, "approved", comment, case_feedback)
    return record


def reject_case(case: CaseFile, settings: Settings, comment: str = "", path: Path | None = None,
                case_feedback: dict[str, FeedbackKind] | None = None) -> dict[str, Any]:
    if case.status != "needs_review":
        raise ValueError(f"cannot reject a case in status {case.status!r}")
    record = _record(case, "rejected", comment)
    _append_jsonl(path or DRAFT_PATH, record)
    record["backend"] = "file"
    record["memory"] = _remember(settings, case, "rejected", comment, case_feedback)
    return record


def cited_cases(case: CaseFile) -> list[dict[str, Any]]:
    """Similar past cases the brief cited: evidence ID, memory ID, summary, trust at run time."""
    out = []
    for evidence_id in case.findings.similar_cases:
        evidence = case.evidence.get(evidence_id)
        if evidence and evidence.content.get("memory_id"):
            out.append({"evidence_id": evidence_id, "memory_id": evidence.content["memory_id"],
                        "summary": evidence.summary, "trust": evidence.content.get("trust")})
    return out


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
    if settings.sample_mode or not settings.pg_app_user:
        _append_jsonl(path, record)
        record["backend"] = "file"
        return
    try:
        _insert_cmms(settings, record)
        record["backend"] = "cmms"
    except Exception as error:
        _append_jsonl(path, record)
        record["backend"] = "file"
        record["cmms_error"] = type(error).__name__


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    try:
        record["path"] = str(path.relative_to(ROOT))
    except ValueError:
        record["path"] = str(path)


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
        for cited in cited_cases(case):
            kind = (case_feedback or {}).get(cited["memory_id"], DEFAULT_CASE_FEEDBACK[decision])
            try:
                trust = store.feedback(cited["memory_id"], kind)
                feedback.append({**cited, "kind": kind, "trust_after": round(trust, 3)})
            except Exception as error:
                feedback.append({**cited, "kind": kind, "error": type(error).__name__})
        return {"ok": True, "backend": store.backend, "result": result, "case_feedback": feedback}
    except Exception as error:
        return {"ok": False, "error": type(error).__name__}
    finally:
        store.close()
