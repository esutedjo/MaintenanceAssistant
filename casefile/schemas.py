"""Case file: the shared state of one run. Agents communicate only through it.

Every number an agent states lives in an Evidence record produced by code; claims cite evidence IDs.
Evidence IDs carry the producing agent's code (EV-DAT-01) so parallel agents never collide.
"""

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_ROUNDS = 3
EVIDENCE_ID = re.compile(r"^EV-[A-Z]{3}-\d{2,}$")
AGENT_CODES = {
    "intake": "INT", "planner": "PLN", "data": "DAT", "prognostics": "PRG", "diagnostics": "DIA",
    "knowledge": "KNW", "maint_planner": "MNT", "rule_checks": "RUL", "critic": "CRT", "escalation": "ESC",
}
# An inspection finding an inspector entered: the data agent records it as EV-INS-nn, not as its own query.
INSPECTION_CODE = "INS"

Confidence = Literal["low", "medium", "high"]
EvidenceKind = Literal["query", "model", "document", "case", "rule", "inspection"]
InspectionKind = Literal["borescope", "visual", "oil_analysis", "chip_detector", "material_test"]
InspectionResult = Literal["within", "at_limit", "beyond"]
RESULT_RANK = {"within": 0, "at_limit": 1, "beyond": 2}
AgentName = Literal["intake", "planner", "data", "prognostics", "diagnostics", "knowledge", "maint_planner",
                    "rule_checks", "critic", "escalation", "engineer"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Every stored time is UTC-aware: a naive legacy value is read as UTC, an offset is converted. Mixing naive and
    aware times broke sorting (a stored naive `entered_at` failed every later brief for the engine)."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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
    resolved_by: str | None = None      # set when an LLM read the engine from free text and code verified it
    recorded: str | None = None         # sample mode: "recorded Nemotron response (captured <date>)"


class Measurement(BaseModel):
    """What the inspector measured: `parameter` names the manual's limit row ("HPC-TIP") or what was measured."""
    parameter: str = Field(min_length=1, max_length=40)
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=16)


class InspectionFindingIn(BaseModel):
    """What an inspector enters (the API body, the UI form), with length limits on every text. The server assigns the
    finding ID, the entry time and the example flag, and a photo only comes from the UI's own upload."""
    asset_id: str = Field(min_length=1, max_length=40)
    kind: InspectionKind
    module: str = Field(min_length=1, max_length=40)  # asset pack inspections.modules: fan, lpc, hpc, ..., oil_system
    location: str = Field("", max_length=120)        # "HPC stage 3"
    observation: str = Field(min_length=1, max_length=2000)
    measurement: Measurement | None = None
    stated_result: InspectionResult | None = None   # the inspector's call; decides only when no limit applies
    inspector: str = Field(min_length=1, max_length=120)
    inspected_on: date                              # UTC date
    cycle: int = Field(ge=0, le=1_000_000)          # engine cycle at inspection

    @field_validator("asset_id", "inspector", "observation", "location", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _classifiable(self):
        if self.measurement is None and self.stated_result is None:
            raise ValueError("a finding needs a measurement or the inspector's result (within, at_limit, beyond)")
        return self

    def to_finding(self) -> "InspectionFinding":
        """The stored finding, with a server-assigned ID and entry time (UTC), never an example."""
        return InspectionFinding(**self.model_dump())


class InspectionFinding(InspectionFindingIn):
    """One inspection result as an inspector entered it (borescope, visual, oil analysis, chip detector, NDT). Stored
    append-only per engine. Code compares a measurement with the manual's limit; without a limit the inspector's own
    result stands and is flagged inspector-classified. A photo is stored by path and never analysed by AI."""
    finding_id: str = Field(default_factory=lambda: f"INS-{uuid.uuid4().hex[:8]}", max_length=64)
    photo: str | None = None
    entered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    example: bool = False                           # the UI's "Load example finding" data, labelled as an example

    @field_validator("entered_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return as_utc(value)


class InspectionAssessment(BaseModel):
    """A finding compared with the limit section Knowledge retrieved (code), or as the inspector classified it when
    no limit applies. `current` is False once a later finding on the same module, location and measure replaces it."""
    finding_id: str
    evidence_id: str                                # EV-INS-nn
    kind: InspectionKind
    module: str
    location: str = ""
    cycle: int
    result: InspectionResult
    classified_by: Literal["limit", "inspector"]
    limit_evidence_id: str | None = None            # EV-KNW-nn: the limit section
    reference: str | None = None                    # "AMM-TF-R3 72-31-00-601"
    limit: dict[str, Any] | None = None             # the limit row: id, name, unit, serviceable, reject
    action: Literal["remove", "reinspect", "routine"]
    interval_cycles: int | None = None              # re-inspection (at limit) or next routine inspection (within)
    fresh: bool = False                             # taken close enough to the last cycle to answer a follow-up
    current: bool = True
    superseded_by: str | None = None
    note: str | None = None


class Task(BaseModel):
    id: str
    agent: AgentName
    description: str
    round: int = 1
    status: Literal["pending", "done", "failed", "skipped"] = "pending"
    kind: str | None = None                     # follow-up or repair the agent runs ("inspection_history", ...)
    reason: str | None = None                   # why the planner delegated it: the finding or gap behind it


class GapRoute(BaseModel):
    """A gate's gap, its kind and the agent that can fix it (None: no agent can, the engineer must decide)."""
    kind: str
    target: AgentName | None
    gap: str


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
    inspections: list[InspectionAssessment] = Field(default_factory=list)
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
    routes: list[GapRoute] = Field(default_factory=list)    # the blocking gaps, by kind and fixing agent
    recorded: str | None = None                 # "recorded Nemotron response (captured <date>)" in sample mode
    # Model notes code showed false or repeating a refuted challenge, as "<why>: <note>"; kept for audit, not shown
    dropped_notes: list[str] = Field(default_factory=list)


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
    # Set only on lines that read a model reply: True for a sample-mode replay of a recorded reply, False for a live
    # call. Left out of the JSONL line when None (code steps), so a replay never reads as a live call.
    recorded: bool | None = None
    confidence: float | None = None
    retry_count: int = 0
    status: Literal["success", "needs_revision", "rejected", "fallback", "error", "skipped"] = "success"
    duration_s: float | None = None
    input_hash: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return as_utc(value)


def merge_evidence(left: dict[str, Evidence], right: dict[str, Evidence]) -> dict[str, Evidence]:
    """Reducer for parallel branches: evidence is append-only; an ID keeps its first record and may never change
    content."""
    merged = dict(left)
    for key, value in right.items():
        if key not in merged:
            merged[key] = value
        elif merged[key].content != value.content:
            raise ValueError(f"evidence {key} was rewritten")
    return merged


def append(left: list, right: list) -> list:
    return [*left, *right]


def merge_tasks(left: list[Task], right: list[Task]) -> list[Task]:
    """Reducer for the plan: the planner adds tasks, agents mark theirs done; a task ID keeps its place."""
    merged = {t.id: t for t in left}
    for task in right:
        merged[task.id] = task
    return list(merged.values())


def keep_error(left: str, right: str) -> str:
    """Reducer for parallel branches: several specialists may fail in one step; "error" is final."""
    return "error" if "error" in (left, right) else right


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


# Follow-ups that need the limit section Knowledge retrieves, so they run after it.
AFTER_KNOWLEDGE = {"inspection_check"}


class CaseFile(BaseModel):
    case_id: str
    question: str
    inspections: list[InspectionFinding] = Field(default_factory=list)  # entered with the question (API, run.py)
    asset: AssetRef | None = None
    intent: Intent | None = None
    plan: Annotated[list[Task], merge_tasks] = Field(default_factory=list)
    evidence: Annotated[dict[str, Evidence], merge_evidence] = Field(default_factory=dict)
    findings: Annotated[Findings, merge_findings] = Field(default_factory=Findings)
    recommendation: Recommendation | None = None
    verdicts: Annotated[list[Verdict], append] = Field(default_factory=list)
    escalations: Annotated[list[Escalation], append] = Field(default_factory=list)
    round: int = Field(default=0, ge=0, le=MAX_ROUNDS)
    trace: Annotated[list[TraceEvent], append] = Field(default_factory=list)
    status: Annotated[Literal["running", "needs_clarification", "needs_review", "approved", "rejected",
                              "insufficient_evidence", "error"], keep_error] = "running"
    degraded: Annotated[list[str], append] = Field(default_factory=list)

    def unresolved(self, evidence_ids: list[str]) -> list[str]:
        return [i for i in evidence_ids if i not in self.evidence]

    def pending(self, agent: str) -> list[Task]:
        """Follow-ups and repairs the planner delegated to this agent and it has not run yet."""
        return [t for t in self.plan if t.agent == agent and t.status == "pending" and t.kind]

    def ready(self, agent: str) -> list[Task]:
        """Pending tasks the agent can run now: one that needs Knowledge's limit section waits until Knowledge ran."""
        knew = any(e.agent_name == "knowledge" for e in self.trace)
        return [t for t in self.pending(agent) if knew or t.kind not in AFTER_KNOWLEDGE]


class EvidenceLedger:
    """Allocates IDs for one agent and collects its evidence; the agent returns ledger.records as its update."""

    def __init__(self, agent: AgentName, existing: dict[str, Evidence] | None = None, code: str | None = None):
        self.agent = agent
        self.prefix = f"EV-{code or AGENT_CODES[agent]}-"
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
