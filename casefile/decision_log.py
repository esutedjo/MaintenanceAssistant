"""Decision log: one chronological record of who decided what and why, for the UI drill-down and the work order.

Agent, gate and model entries are derived from what the case file already records (trace, verdicts, escalations,
document findings): the planner's follow-up delegations and gap routing come from its handoff trace lines, so no
agent writes them. The engineer's sign-off is the one entry added at decision time.
Persisted per case as append-only JSONL with a SHA-256 hash chain, so an edited, deleted or reordered entry shows."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field, field_validator

from casefile.schemas import MAX_ROUNDS, CaseFile, Escalation, TraceEvent, Verdict, utcnow

ActorType = Literal["agent", "gate", "model", "human"]
GATES = {"rule_checks", "critic", "escalation", "close"}
OUTCOME = {"accept": "accepted", "revise": "sent back for revision", "reject": "rejected"}
CONFIDENCE = {0.3: "low", 0.6: "medium", 0.9: "high"}       # maint_planner trace confidence
TASK_AGENTS = {"data", "prognostics", "diagnostics", "knowledge"}
TASK_DECISION = {"follow_up": "ran follow-up", "repair": "ran repair"}
HANDOFF_DECISION = {"delegate": "delegated follow-up", "route": "routed gap"}
ROUND = re.compile(r"\bround=(\d+)")
# Wording of agents/critic.py checked_challenges; tests/test_decision_log.py keeps the two in step.
REFUTED = "challenge refuted by code"
CONFIRMED = "confirmed by code)"
EVIDENCE_REF = re.compile(r"\bEV-[A-Z]{3}-\d{2,}\b")
GENESIS = "0" * 64
CHAIN_FIELDS = ("seq", "prev_hash", "hash")
SIGNOFF_NOTE = {
    "approved": ("Approved the brief, which drafts a work order for the organisation's own release process and is "
                 "not a certificate of release to service."),
    "rejected": "Rejected the brief, so no work order is drafted.",
}


class Signer(BaseModel):
    """Who signed, as typed at sign-off. Cleris does not check the name against a licence register."""
    name: str = Field(min_length=1)
    role: str | None = None
    licence_ref: str | None = None

    @field_validator("name", "role", "licence_ref", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return (value.strip() or None) if isinstance(value, str) else value


class Acknowledgement(BaseModel):
    """An escalation the engineer ticked as read before signing, or an info note shown at sign-off."""
    rule: str
    severity: str
    message: str
    reason: str | None = None           # approve only: why the engineer approved despite this urgent escalation


class BriefRef(BaseModel):
    """The exact brief a sign-off refers to: the recommendation hash changes if any field of the plan changes."""
    case_id: str
    run_id: str
    recommendation_sha256: str | None


class BriefSnapshot(BaseModel):
    """The whole brief as shown at sign-off (the case JSON: evidence, the document sections used, the plan), stored
    beside the log; the SHA-256 is of the stored file's bytes."""
    path: str
    sha256: str


class DecisionEntry(BaseModel):
    timestamp: datetime
    run_id: str
    actor_type: ActorType
    actor: str                          # agent or gate name, model id, or the engineer's name
    decision: str                       # short verb phrase: "proposed plan", "sent back for revision", "approved"
    rationale: str                      # one plain sentence
    evidence_ids: list[str] = Field(default_factory=list)
    round: int | None = None            # revise round, when the decision belongs to one
    # Sign-off entries only
    signer: Signer | None = None
    comment: str | None = None
    case_feedback: dict[str, str] = Field(default_factory=dict)    # cited past case evidence ID -> rating
    acknowledged: list[Acknowledgement] = Field(default_factory=list)
    noted: list[Acknowledgement] = Field(default_factory=list)     # info escalations shown as notes: recorded as seen
    brief: BriefRef | None = None
    brief_snapshot: BriefSnapshot | None = None
    wo_id: str | None = None
    # Set when the entry is persisted: code, asset pack, model artifact and LLM versions the case ran on
    versions: dict[str, Any] | None = None
    seq: int | None = None
    prev_hash: str | None = None
    hash: str | None = None


def derive_log(case: CaseFile) -> list[DecisionEntry]:
    """Agent, gate and model decisions in trace order. Each gate trace line is paired with that gate's next verdict."""
    verdicts = {gate: iter([v for v in case.verdicts if v.gate == gate]) for gate in ("rule_checks", "critic")}
    entries, plans, round_no = [], 0, None
    for event in case.trace:
        if event.status == "error":
            entries.append(_entry(event, _actor_type(event.agent_name), event.agent_name, "failed",
                                  event.output_summary))
        elif event.agent_name == "maint_planner":
            plans += 1
            entries.append(_proposal(event, plans))
        elif event.agent_name in verdicts:
            verdict = next(verdicts[event.agent_name], None)
            if verdict is not None:
                entries.extend(_rule_verdict(event, verdict) if event.agent_name == "rule_checks"
                               else _critic(event, verdict))
        elif event.agent_name == "planner":
            if event.action == "route_gaps":
                match = ROUND.search(event.input_summary)
                round_no = int(match.group(1)) if match else round_no
            entries.extend(_planner(event, round_no))
        elif event.agent_name in TASK_AGENTS and event.action in TASK_DECISION:
            entries.append(_entry(event, "agent", event.agent_name, TASK_DECISION[event.action],
                                  event.output_summary, event.evidence_ids,
                                  round_no if event.action == "repair" else None))
        else:
            entries.extend(_other(case, event))
    return entries


def run_id_of(case: CaseFile) -> str:
    return case.trace[0].run_id if case.trace else case.case_id


def brief_ref(case: CaseFile) -> BriefRef:
    rec = case.recommendation
    return BriefRef(case_id=case.case_id, run_id=run_id_of(case),
                    recommendation_sha256=_digest(rec.model_dump(mode="json")) if rec else None)


def signoff_entry(case: CaseFile, decision: Literal["approved", "rejected"], signer: Signer, comment: str = "",
                  ratings: dict[str, str] | None = None, wo_id: str | None = None,
                  acknowledged: list[Escalation] | None = None, reason: str = "",
                  noted: list[Escalation] | None = None, snapshot: BriefSnapshot | None = None) -> DecisionEntry:
    """The engineer's decision, bound to the brief it refers to, the escalations ticked as read and the info notes
    shown beside them. Time is UTC. `reason` (approve only) is stored on each urgent escalation the engineer approved
    despite. `snapshot` is the stored copy of the brief as shown."""
    reason = ((reason or "").strip() or None) if decision == "approved" else None
    return DecisionEntry(timestamp=utcnow(), run_id=run_id_of(case), actor_type="human", actor=signer.name,
                         decision=decision, rationale=SIGNOFF_NOTE[decision], evidence_ids=list(ratings or {}),
                         round=case.round or None, signer=signer, comment=(comment or "").strip() or None,
                         case_feedback=dict(ratings or {}), brief=brief_ref(case), brief_snapshot=snapshot,
                         wo_id=wo_id,
                         acknowledged=[Acknowledgement(rule=e.rule, severity=e.severity, message=e.message,
                                                       reason=reason if e.severity == "urgent" else None)
                                       for e in acknowledged or []],
                         noted=[Acknowledgement(rule=e.rule, severity=e.severity, message=e.message)
                                for e in noted or []])


def log_path(log_dir: Path, case_id: str) -> Path:
    return log_dir / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', case_id)}.jsonl"


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_entries(path: Path, entries: list[DecisionEntry]) -> list[dict[str, Any]]:
    """Append the entries not yet in the file, each chained to the hash before it. Existing lines are never
    rewritten; a correction is a new entry. Returns every row in the file."""
    rows = read_log(path)
    logged = Counter(_canonical(_body(row)) for row in rows)
    prev, new = (rows[-1]["hash"] if rows else GENESIS), []
    for entry in entries:
        body = entry.model_dump(mode="json", exclude=set(CHAIN_FIELDS))
        if logged[_canonical(body)] > 0:
            logged[_canonical(body)] -= 1
            continue
        row = {**body, "seq": len(rows) + len(new) + 1, "prev_hash": prev}
        row["hash"] = prev = _digest(row)
        new.append(row)
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.writelines(json.dumps(row) + "\n" for row in new)
    return [*rows, *new]


def verify_rows(rows: list[dict[str, Any]], head_hash: str | None = None) -> tuple[bool, str]:
    """Walk the hash chain: (intact, reason). `head_hash` (from the work order) also catches entries cut from the end."""
    prev = GENESIS
    for n, row in enumerate(rows, 1):
        if row.get("seq") != n:
            return False, f"entry {n} has sequence {row.get('seq')}: an entry was deleted or reordered"
        if row.get("prev_hash") != prev:
            return False, f"entry {n} does not chain to the entry before it"
        if _digest({k: v for k, v in row.items() if k != "hash"}) != row.get("hash"):
            return False, f"entry {n} does not match its hash: it was edited"
        prev = row["hash"]
    if head_hash is not None and prev != head_hash:
        return False, "the last entry is not the one the work order references: entries were removed"
    return True, f"hash chain intact, {len(rows)} entries"


def verify_log(path: Path, head_hash: str | None = None) -> tuple[bool, str]:
    try:
        rows = read_log(path)
    except json.JSONDecodeError as error:
        return False, f"line {error.lineno} is not valid JSON"
    return verify_rows(rows, head_hash)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _body(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in CHAIN_FIELDS}


def _actor_type(agent: str) -> ActorType:
    return "gate" if agent in GATES else "agent"


def _sentence(text: str) -> str:
    text = " ".join((text or "").split())
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _refs(texts: list[str]) -> list[str]:
    return [m for text in texts for m in EVIDENCE_REF.findall(text)]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _entry(event: TraceEvent, actor_type: ActorType, actor: str, decision: str, rationale: str,
           evidence_ids: list[str] | None = None, round_no: int | None = None) -> DecisionEntry:
    return DecisionEntry(timestamp=event.timestamp, run_id=event.run_id, actor_type=actor_type, actor=actor,
                         decision=decision, rationale=_sentence(rationale),
                         evidence_ids=list(dict.fromkeys(evidence_ids or [])), round=round_no)


def _other(case: CaseFile, event: TraceEvent) -> Iterator[DecisionEntry]:
    if event.agent_name == "intake":
        yield from _intake(case, event)
    elif event.agent_name == "knowledge" and event.action == "retrieve_documents":
        yield from _mandates(case, event)
    elif event.agent_name == "escalation":
        yield from _escalations(case, event)
    elif event.agent_name == "close":
        yield _close(case, event)


def _intake(case: CaseFile, event: TraceEvent) -> Iterator[DecisionEntry]:
    if event.status == "needs_revision":
        if event.model:
            yield _entry(event, "model", event.model, "no engine confirmed",
                         "Its reading of the question named no engine that code could confirm in the question and "
                         "the asset registry")
        yield _entry(event, "agent", "intake", "asked back", event.output_summary)
        return
    asset = case.asset.asset_id if case.asset else (case.intent.asset_id if case.intent else "the engine")
    resolved_by = case.intent.resolved_by if case.intent else None
    if resolved_by:
        replayed = f" ({case.intent.recorded})" if case.intent.recorded else ""
        yield _entry(event, "model", resolved_by, "read the engine from free text",
                     f"Named {asset}; code found the quoted words in the question and the engine in the "
                     f"registry{replayed}", event.evidence_ids)
    yield _entry(event, "agent", "intake", "resolved engine",
                 f"The question resolves to {asset}, found in the asset registry", event.evidence_ids)


def _planner(event: TraceEvent, round_no: int | None) -> list[DecisionEntry]:
    """Its review after round 1 (follow-ups or none needed) and its routing of each gap a gate raised."""
    if event.action in HANDOFF_DECISION:
        repair = event.action == "route"
        return [_entry(event, "agent", "planner", HANDOFF_DECISION[event.action],
                       f"To {event.target_agent}: {event.output_summary}", _refs([event.output_summary]),
                       round_no if repair else None)]
    if event.action == "review_findings" and event.target_agent == "knowledge":
        return [_entry(event, "agent", "planner", "no follow-up needed", event.output_summary.split(": ", 1)[-1])]
    if event.action == "route_gaps" and event.target_agent == "close":
        return [_entry(event, "agent", "planner", "no agent can fix", event.output_summary, round_no=round_no)]
    return []


def _mandates(case: CaseFile, event: TraceEvent) -> Iterator[DecisionEntry]:
    for doc in case.findings.documents:
        if doc.mandatory and doc.required_task:
            within = f" within {doc.within_cycles} cycles" if doc.within_cycles is not None else ""
            yield _entry(event, "agent", "knowledge", "applied mandate",
                         f"{doc.doc_id} {doc.section} requires {doc.required_task}{within}; agents cannot remove it",
                         [doc.evidence_id])


def _proposal(event: TraceEvent, number: int) -> DecisionEntry:
    confidence = CONFIDENCE.get(event.confidence)
    text = event.output_summary + (f", confidence {confidence}" if confidence else "")
    return _entry(event, "agent", "maint_planner", "proposed plan" if number == 1 else "revised plan", text,
                  event.evidence_ids, number)


def _gate_outcome(verdict: Verdict) -> str:
    if verdict.outcome == "revise" and verdict.round >= MAX_ROUNDS:
        return "not accepted at the round limit"
    return OUTCOME[verdict.outcome]


def _rule_verdict(event: TraceEvent, verdict: Verdict) -> list[DecisionEntry]:
    if verdict.outcome == "accept":
        return [_entry(event, "gate", "rule_checks", "accepted", "All deterministic rule checks passed",
                       round_no=verdict.round)]
    gaps = verdict.gaps
    return [_entry(event, "gate", "rule_checks", _gate_outcome(verdict),
                   f"{_count(len(gaps), 'check')} failed: {'; '.join(gaps)}", _refs(gaps), verdict.round)]


def _critic(event: TraceEvent, verdict: Verdict) -> list[DecisionEntry]:
    """With a model, unconfirmed gaps are its notes (they never block a cited brief); without one they are the
    deterministic checks' gaps. Each challenge the model raised is one gate entry: confirmed or refuted by code."""
    refuted = [g for g in verdict.gaps if REFUTED in g]
    confirmed = [g for g in verdict.gaps if CONFIRMED in g]
    others = [g for g in verdict.gaps if g not in refuted and g not in confirmed]
    model, round_no = verdict.model, verdict.round
    blocking = confirmed + ([] if model else others)
    entries = []
    if model:
        replayed = f" ({verdict.recorded})" if verdict.recorded else ""
        entries.append(_entry(event, "model", model, "reviewed the brief",
                              f"Raised {_count(len(confirmed) + len(refuted), 'checkable challenge')} and "
                              f"{_count(len(others), 'note')} for the engineer; notes cannot block a cited "
                              f"brief{replayed}", _refs(others), round_no))
    entries += [_entry(event, "gate", "critic", "challenge confirmed", g, _refs([g]), round_no) for g in confirmed]
    entries += [_entry(event, "gate", "critic", "challenge refuted", g, _refs([g]), round_no) for g in refuted]
    if verdict.outcome == "accept":
        reason = "No gap found by the deterministic checks" + ("; no model challenge was confirmed by code"
                                                               if model else "")
    else:
        reason = f"{_count(len(blocking), 'gap')} to fix: {'; '.join(blocking)}"
    entries.append(_entry(event, "gate", "critic", _gate_outcome(verdict), reason, _refs(blocking), round_no))
    return entries


def _escalations(case: CaseFile, event: TraceEvent) -> Iterator[DecisionEntry]:
    round_no = case.round or None
    if not case.escalations:
        yield _entry(event, "gate", "escalation", "no escalation", "No escalation rule fired", round_no=round_no)
    for esc in case.escalations:
        yield _entry(event, "gate", "escalation", "escalated",
                     f"{esc.message.rstrip('.')} ({esc.severity}, rule {esc.rule})", esc.evidence_ids, round_no)


def _close(case: CaseFile, event: TraceEvent) -> DecisionEntry:
    gaps = list(dict.fromkeys(g for v in case.verdicts if v.outcome != "accept" for g in v.gaps))
    return _entry(event, "gate", "close", "closed: insufficient evidence",
                  f"No brief passed the gates in {_count(case.round, 'round')}, so the case closes with "
                  f"{_count(len(gaps), 'open gap')} for the engineer", _refs(gaps), case.round or None)
