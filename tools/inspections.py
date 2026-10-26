"""Inspection findings: the per-engine store, the manual's limit sections and the comparison of a finding with them.

Findings are what an inspector entered (borescope, visual, oil analysis, chip detector, NDT). They are kept append-only
as .state/inspections/<asset>.jsonl (beside the case-memory SQLite file; the same local file in live mode, the CMMS
schema is unchanged); a correction is a new finding. Limits live in the asset pack's documents as limit sections
(topic "limits:<kind>:<name>", one machine-readable line per limit row), so the knowledge agent retrieves and cites
them like any other section and the revision in effect decides. The comparison is code: a measured value against the
row's serviceable and reject limits. Without a limit the inspector's own result stands, flagged inspector-classified.
Nothing here asks a model, and a photo is stored by path, never analysed."""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from casefile.schemas import RESULT_RANK, InspectionAssessment, InspectionFinding, InspectionResult
from settings import Settings
from tools.data_access import safe_id

LIMIT_TOPIC = "limits:"
LIMIT_ROW = re.compile(r"Limit (?P<id>[A-Z0-9-]+): (?P<name>[^;]+), (?P<unit>[^,;]+); serviceable up to "
                       r"(?P<serviceable>-?\d+(?:\.\d+)?); reject above (?P<reject>-?\d+(?:\.\d+)?)\.")
INTERVAL = re.compile(r"Interval: within limits next inspection (?P<routine>\d+) cycles; at limit re-inspect within "
                      r"(?P<reinspect>\d+) cycles")
ACTION: dict[str, str] = {"within": "routine", "at_limit": "reinspect", "beyond": "remove"}
RESULT_TEXT = {"within": "within limits", "at_limit": "at the limit", "beyond": "beyond limits"}


# --- Store -----------------------------------------------------------------------------------------------------

def store_dir(settings: Settings) -> Path:
    """Local state lives beside the case-memory SQLite file (.state/ by default; a test's temp folder in tests)."""
    return Path(settings.context_sqlite_path).parent / "inspections"


def store_path(settings: Settings, asset_id: str) -> Path:
    return store_dir(settings) / f"{safe_id(asset_id)}.jsonl"


def load_findings(settings: Settings, asset_id: str) -> list[InspectionFinding]:
    path = store_path(settings, asset_id)
    if not path.exists():
        return []
    return [InspectionFinding.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def record_finding(settings: Settings, finding: InspectionFinding) -> Path:
    """Append one finding to its engine's file. Lines are never rewritten; an ID already stored is refused."""
    path = store_path(settings, finding.asset_id)
    if any(f.finding_id == finding.finding_id for f in load_findings(settings, finding.asset_id)):
        raise ValueError(f"finding {finding.finding_id} is already recorded; a correction is a new finding")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(finding.model_dump_json() + "\n")
    return path


def merged(stored: list[InspectionFinding], given: list[InspectionFinding]) -> list[InspectionFinding]:
    """Stored findings plus the ones entered with the question, each ID once, oldest inspection first."""
    by_id = {f.finding_id: f for f in [*stored, *given]}
    return sorted(by_id.values(), key=lambda f: (f.cycle, f.inspected_on, f.entered_at))


# --- Limit sections -------------------------------------------------------------------------------------------

def is_limit_section(chunk: dict) -> bool:
    return str(chunk.get("topic") or "").startswith(LIMIT_TOPIC)


def section_kind(chunk: dict) -> str:
    return str(chunk.get("topic") or "").split(":")[1] if is_limit_section(chunk) else ""


def parse_limits(text: str) -> dict[str, Any]:
    """The limit rows and the two intervals a limit section states."""
    rows = [{"id": m["id"], "name": m["name"].strip(), "unit": m["unit"].strip(),
             "serviceable": float(m["serviceable"]), "reject": float(m["reject"])} for m in LIMIT_ROW.finditer(text)]
    interval = INTERVAL.search(text)
    return {"rows": rows, "routine_cycles": int(interval["routine"]) if interval else None,
            "reinspect_cycles": int(interval["reinspect"]) if interval else None}


def label(chunk: dict) -> str:
    return f"{chunk['doc_id']} {chunk['section']}"


def limit_section(chunks: list[dict], pack: dict, kind: str, module: str, on: date, variant: str | None
                  ) -> tuple[dict | None, list[dict]]:
    """The limit section for this kind of inspection on this module: in effect on the date and for the variant,
    highest document precedence first, then the latest effective date. Returns it and the ones set aside, with why."""
    from tools.knowledge import in_effect

    order = {t: i for i, t in enumerate((pack.get("documents") or {}).get("precedence") or [])}
    candidates, excluded = [], []
    for chunk in chunks:
        if not is_limit_section(chunk) or section_kind(chunk) != kind:
            continue
        if module not in str(chunk.get("components") or "").split(","):
            continue
        reason = in_effect(chunk, on)
        if not reason and variant and variant not in str(chunk.get("applies_to") or "").split(","):
            reason = f"not applicable to {variant}"
        if reason:
            excluded.append({"chunk": label(chunk), "reason": reason})
        else:
            candidates.append(chunk)
    newest_first = lambda c: -date.fromisoformat(str(c["effective_date"])[:10]).toordinal()  # noqa: E731
    candidates.sort(key=lambda c: (order.get(c["doc_type"], len(order)), newest_first(c)))
    for loser in candidates[1:]:
        excluded.append({"chunk": label(loser), "reason": f"lower precedence than {label(candidates[0])}"})
    return (candidates[0] if candidates else None), excluded


def find_row(rows: list[dict], parameter: str) -> dict | None:
    """The limit row a measurement names, by its ID ("HPC-TIP") or its name ("blade tip erosion")."""
    wanted = " ".join(parameter.lower().split())
    return next((r for r in rows if wanted in {r["id"].lower(), r["name"].lower()}), None)


def classify(value: float, row: dict) -> InspectionResult:
    """Code, not a person or a model: up to the serviceable limit is within, up to the reject limit is at the limit,
    above it is beyond."""
    if value <= row["serviceable"]:
        return "within"
    return "at_limit" if value <= row["reject"] else "beyond"


@lru_cache(maxsize=4)
def _pack_limit_chunks(pack_json: str) -> tuple[dict, ...]:
    from tools.knowledge import parse_document
    from settings import ROOT

    pack = json.loads(pack_json)
    spec = pack.get("documents") or {}
    folder = ROOT / spec.get("directory", "")
    return tuple(c for name in spec.get("files") or [] for c in parse_document(folder / name) if is_limit_section(c))


def pack_limit_chunks(pack: dict) -> list[dict]:
    """The limit sections of the asset pack's own documents (the UI form, the seeded eval fault)."""
    return list(_pack_limit_chunks(json.dumps(pack, sort_keys=True, default=str)))


def limit_catalog(pack: dict, on: date, variant: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """(kind, module) -> the limit section in effect and its rows, for the form's "measured" choices."""
    chunks = pack_limit_chunks(pack)
    out = {}
    for kind in (pack.get("inspections") or {}).get("kinds") or {}:
        for module in (pack.get("inspections") or {}).get("modules") or {}:
            chunk, _ = limit_section(chunks, pack, kind, module, on, variant)
            if chunk is not None:
                out[(kind, module)] = {"reference": label(chunk), "heading": chunk["heading"],
                                       **parse_limits(chunk["text"])}
    return out


# --- Assessment -----------------------------------------------------------------------------------------------

def assess(finding: InspectionFinding, evidence_id: str, *, limit_evidence_id: str, section: dict | None,
           limits: dict[str, Any] | None, pack: dict, fresh: bool) -> InspectionAssessment:
    """Compare one finding with its limit section. A measurement with a matching row (same unit) is classified by
    code; otherwise the inspector's stated result stands, flagged inspector-classified, with the reason."""
    rows = (limits or {}).get("rows") or []
    measured = finding.measurement
    row = find_row(rows, measured.parameter) if measured and rows else None
    note = None
    if row is not None and measured.unit.strip().lower() != row["unit"].lower():
        note = f"measured in {measured.unit}, the limit is in {row['unit']}; not compared"
        row = None
    if row is not None:
        result: InspectionResult = classify(measured.value, row)
        by = "limit"
        if finding.stated_result and finding.stated_result != result:
            note = (f"the inspector stated {RESULT_TEXT[finding.stated_result]}; the measurement against the limit "
                    f"is {RESULT_TEXT[result]}, which decides")
    else:
        if section is None:
            why = "no limit section in the documents covers this inspection"
        elif measured is None:
            why = f"no measurement to compare with {label(section)}"
        else:
            why = note or f"{label(section)} has no limit row for {measured.parameter}"
        if finding.stated_result is None:
            raise ValueError(f"finding {finding.finding_id}: {why}, and the inspector stated no result")
        result, by, note = finding.stated_result, "inspector", why
    default = int((pack.get("inspections") or {}).get("default_reinspect_cycles") or 10)
    interval = {"within": (limits or {}).get("routine_cycles"),
                "at_limit": (limits or {}).get("reinspect_cycles") or default, "beyond": None}[result]
    return InspectionAssessment(
        finding_id=finding.finding_id, evidence_id=evidence_id, kind=finding.kind, module=finding.module,
        location=finding.location, cycle=finding.cycle, result=result, classified_by=by,
        limit_evidence_id=limit_evidence_id, reference=label(section) if section else None, limit=row,
        action=ACTION[result], interval_cycles=interval, fresh=fresh, note=note)


def mark_current(pairs: list[tuple[InspectionAssessment, InspectionFinding]]) -> list[InspectionAssessment]:
    """Per module, location, inspection kind and measure, the latest inspection stands; at the same cycle the worse
    result stands, so a second entry cannot soften a finding without a new inspection."""
    groups: dict[tuple, list[tuple[InspectionAssessment, InspectionFinding]]] = {}
    for assessment, finding in pairs:
        measure = (assessment.limit or {}).get("id") or (finding.measurement.parameter.lower()
                                                         if finding.measurement else "")
        key = (assessment.kind, assessment.module, " ".join(assessment.location.lower().split()), measure)
        groups.setdefault(key, []).append((assessment, finding))
    out = []
    for group in groups.values():
        winner = max(group, key=lambda p: (p[0].cycle, RESULT_RANK[p[0].result], p[1].entered_at))[0]
        out += [a if a is winner else a.model_copy(update={"current": False, "superseded_by": winner.evidence_id})
                for a, _ in group]
    return sorted(out, key=lambda a: a.evidence_id)


def mode_for_module(pack: dict, module: str, variant: str | None) -> str | None:
    """The fault mode of this fleet variant that wears the module, if any (hpc -> hpc_degradation)."""
    modes = (pack.get("variants") or {}).get(variant or "", list(pack.get("failure_modes") or {}))
    return next((m for m in modes if (pack["failure_modes"].get(m) or {}).get("component") == module), None)


def where(finding: dict | InspectionFinding, pack: dict) -> str:
    """"borescope, HPC stage 3" from a finding or its stored dict."""
    f = finding.model_dump() if isinstance(finding, InspectionFinding) else finding
    spec = pack.get("inspections") or {}
    kind = (spec.get("kinds") or {}).get(f["kind"], f["kind"]).split(" (")[0].lower()
    module = (spec.get("modules") or {}).get(f["module"], f["module"])
    module = module if module.isupper() else module.lower()          # HPC stays HPC, "Oil system" reads "oil system"
    location = f.get("location") or ""
    return f"{kind}, {location if module.lower() in location.lower() else ' '.join([module, location]).strip()}"


def measured_text(finding: dict) -> str:
    m = finding.get("measurement")
    return f"{m['parameter']} {m['value']:g} {m['unit']}" if m else "no measurement"


# --- Example findings (UI demo and input_examples; labelled as example data) ----------------------------------

EXAMPLES: dict[str, dict[str, Any]] = {
    "within": {"kind": "borescope", "module": "hpc", "location": "HPC stage 3",
               "observation": "Light tip erosion on stage 3 blades; leading edges intact; no nicks, cracks or "
                              "coating loss.",
               "measurement": {"parameter": "HPC-TIP", "value": 0.42, "unit": "mm"}},
    "beyond": {"kind": "borescope", "module": "hpc", "location": "HPC stage 3",
               "observation": "Tip erosion on several stage 3 blades with curl on the worst blade; leading-edge "
                              "material loss.",
               "measurement": {"parameter": "HPC-TIP", "value": 0.82, "unit": "mm"}},
}


def example_finding(which: str, asset_id: str, cycle: int, on: date) -> InspectionFinding:
    """A synthetic HPC borescope result for the demo, marked as example data."""
    return InspectionFinding(asset_id=asset_id, cycle=cycle, inspected_on=on, inspector="Example inspector",
                             example=True, **EXAMPLES[which])
