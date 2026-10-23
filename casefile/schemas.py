"""Case file: the shared state of one run. Agents communicate only through it.

Every number an agent states lives in an Evidence record produced by code; claims cite evidence IDs.
Evidence IDs carry the producing agent's code (EV-DAT-01) so parallel agents never collide.
"""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_ROUNDS = 3
EVIDENCE_ID = re.compile(r"^EV-[A-Z]{3}-\d{2,}$")
AGENT_CODES = {
    "intake": "INT", "planner": "PLN", "data": "DAT", "prognostics": "PRG", "diagnostics": "DIA",
    "knowledge": "KNW", "maint_planner": "MNT", "rule_checks": "RUL", "critic": "CRT", "escalation": "ESC",
}

Confidence = Literal["low", "medium", "high"]
EvidenceKind = Literal["query", "model", "document", "case", "rule"]
AgentName = Literal["intake", "planner", "data", "prognostics", "diagnostics", "knowledge", "maint_planner",
                    "rule_checks", "critic", "escalation", "engineer"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


class Evidence(BaseModel):
    id: str
    kind: EvidenceKind
    source: str                    # "zetaris:cmms_pg.work_orders", "snapshot:cycles.parquet", "model:rul_a"
    summary: str                   # one line a human (and the LLM) reads instead of raw rows
    content: dict[str, Any]
    produced_by: AgentName
    created_at: datetime = Field(default_factory=utcnow)
    degraded: str | None = None    # set when produced from a fallback, with the reason

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not EVIDENCE_ID.match(value):
            raise ValueError(f"evidence id {value!r} does not match EV-XXX-NN")
        return value


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: Confidence


class AssetRef(BaseModel):
    asset_id: str
    asset_type: str
    pack_version: str
    variant: str | None = None                  # fleet variant, keys asset pack `variants` (e.g. FD003)


class Intent(BaseModel):
    goal: Literal["status", "remaining_life", "diagnosis", "maintenance_plan", "full_assessment"]
    asset_id: str | None
    horizon_days: int | None = None
    needs_clarification: bool = False
    clarification: str | None = None


class Task(BaseModel):
    id: str
    agent: AgentName
    description: str
    round: int = 1
    status: Literal["pending", "done", "failed", "skipped"] = "pending"


class RulFinding(BaseModel):
    method: Literal["lightgbm", "health_index"]
    rul: float = Field(ge=0)
    lower: float = Field(ge=0)
    upper: float = Field(ge=0)
    at_cap: bool
    note: str | None = None
    evidence_id: str


class FaultModeFinding(BaseModel):
    failure_mode: str
    probability: float = Field(ge=0, le=1)
    evidence_id: str


class Driver(BaseModel):
    sensor: str
    component: str
    contribution: float
    evidence_id: str


class AnomalyFinding(BaseModel):
    cycle: int
    health_index: float
    fleet_median: float
    residual_sigma: float
    flagged: bool
    evidence_id: str


class DocumentFinding(BaseModel):
    """A document section that applies to the case. Mandatory ones must stay in the plan (rule check)."""
    doc_id: str
    doc_type: str
    section: str
    heading: str
    revision: str
    effective_date: str
    precedence: int                             # 0 = highest (asset pack `documents.precedence`)
    relevance: float
    failure_modes: list[str] = Field(default_factory=list)
    mandatory: bool = False
    required_task: str | None = None            # FMEA task the mandate requires
    within_cycles: int | None = None
    action: str | None = None
    overridden_by: str | None = None            # "SB-TF-72-0142 3.B" when a higher document wins the topic
    evidence_id: str


class Findings(BaseModel):
    rul_ml: RulFinding | None = None
    rul_trend: RulFinding | None = None
    fault_modes: list[FaultModeFinding] = Field(default_factory=list)
    drivers: list[Driver] = Field(default_factory=list)
    anomaly: AnomalyFinding | None = None
    similar_cases: list[str] = Field(default_factory=list)     # evidence IDs of kind "case"
    documents: list[DocumentFinding] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)


class PartNeed(BaseModel):
    part_number: str
    qty: int = Field(gt=0)
    available: int
    lead_time_days: int
    evidence_ids: list[str] = Field(min_length=1)


class Recommendation(BaseModel):
    action: str
    task_ids: list[str]                         # FMEA task IDs, e.g. FM-HPC-01
    parts: list[PartNeed] = Field(default_factory=list)
    slot: date | None = None
    deadline: date | None = None
    rationale: list[Claim] = Field(min_length=1)
    confidence: Confidence


class Verdict(BaseModel):
    round: int
    gate: Literal["rule_checks", "critic"]
    outcome: Literal["accept", "revise", "reject"]
    gaps: list[str] = Field(default_factory=list)
    model: str | None = None


class Escalation(BaseModel):
    rule: str                                   # e.g. "rul_below_lead_time"
    severity: Literal["info", "warning", "urgent"]
    message: str
    actions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)


class TraceEvent(BaseModel):
    """One line of logs/trace-<run_id>.jsonl, in the format the hackathon judges read."""

    timestamp: datetime = Field(default_factory=utcnow)
    run_id: str
    agent_name: str
    action: str
    input_summary: str
    output_summary: str
    target_agent: str | None = None
    model: str | None = None
    confidence: float | None = None
    retry_count: int = 0
    status: Literal["success", "needs_revision", "rejected", "fallback", "error", "skipped"] = "success"
    duration_s: float | None = None
    input_hash: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


def merge_evidence(left: dict[str, Evidence], right: dict[str, Evidence]) -> dict[str, Evidence]:
    """Reducer for parallel branches: evidence is append-only; an ID may never change content."""
    merged = dict(left)
    for key, value in right.items():
        if key in merged and merged[key].content != value.content:
            raise ValueError(f"evidence {key} was rewritten")
        merged[key] = value
    return merged


def append(left: list, right: list) -> list:
    return [*left, *right]


def merge_findings(left: Findings, right: Findings) -> Findings:
    """Reducer for parallel branches: each agent fills its own fields; lists are concatenated."""
    merged = left.model_copy()
    for name in Findings.model_fields:
        value = getattr(right, name)
        if isinstance(value, list):
            setattr(merged, name, [*getattr(left, name), *value])
        elif value is not None:
            setattr(merged, name, value)
    return merged


class CaseFile(BaseModel):
    case_id: str
    question: str
    asset: AssetRef | None = None
    intent: Intent | None = None
    plan: list[Task] = Field(default_factory=list)
    evidence: Annotated[dict[str, Evidence], merge_evidence] = Field(default_factory=dict)
    findings: Annotated[Findings, merge_findings] = Field(default_factory=Findings)
    recommendation: Recommendation | None = None
    verdicts: Annotated[list[Verdict], append] = Field(default_factory=list)
    escalations: Annotated[list[Escalation], append] = Field(default_factory=list)
    round: int = Field(default=0, ge=0, le=MAX_ROUNDS)
    trace: Annotated[list[TraceEvent], append] = Field(default_factory=list)
    status: Literal["running", "needs_clarification", "needs_review", "approved", "rejected", "insufficient_evidence",
                    "error"] = "running"
    degraded: Annotated[list[str], append] = Field(default_factory=list)

    def unresolved(self, evidence_ids: list[str]) -> list[str]:
        return [i for i in evidence_ids if i not in self.evidence]


class EvidenceLedger:
    """Allocates IDs for one agent and collects its evidence; the agent returns ledger.records as its update."""

    def __init__(self, agent: AgentName, existing: dict[str, Evidence] | None = None):
        self.agent = agent
        self.prefix = f"EV-{AGENT_CODES[agent]}-"
        taken = [int(k[len(self.prefix):]) for k in (existing or {}) if k.startswith(self.prefix)]
        self._next = max(taken, default=0) + 1
        self.records: dict[str, Evidence] = {}

    def add(self, kind: EvidenceKind, source: str, summary: str, content: dict[str, Any],
            degraded: str | None = None) -> Evidence:
        evidence = Evidence(id=f"{self.prefix}{self._next:02d}", kind=kind, source=source, summary=summary,
                            content=content, produced_by=self.agent, degraded=degraded)
        self._next += 1
        self.records[evidence.id] = evidence
        return evidence
