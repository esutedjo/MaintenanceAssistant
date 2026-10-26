"""Work packages: what a plan means on the hangar floor. The asset pack's templates (work_packages.yaml, synthetic task
references) name the steps, the required inspection items, tooling, manhours and skills per kind of work; this module
picks the templates whose FMEA tasks are in the plan and fills in when each part happens. Data, not agent code: a new
asset type brings its own templates."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from casefile.schemas import WorkPackage, WorkSection, WorkTask
from settings import ROOT


@lru_cache(maxsize=4)
def _read(path: str, mtime_ns: int) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_templates(pack: dict) -> dict[str, Any]:
    """The pack's work-package file ({note, templates}); empty when the pack has none."""
    name = pack.get("work_packages")
    if not name:
        return {}
    path = ROOT / "asset_packs" / pack["asset_type"] / name
    return _read(str(path), path.stat().st_mtime_ns) if path.exists() else {}


def build(pack: dict, task_ids: list[str], *, slot: date | None, deadline: date | None, removal: bool,
          governed_by: dict[str, str] | None = None) -> dict[str, Any] | None:
    """The work package for the plan's tasks as plain data (the planner stores it as evidence and types it), or None
    when no template covers any of them. `removal`: the plan takes the engine off the wing, so an inspection the plan
    keeps happens at shop induction; `governed_by`: task -> the document whose limits and intervals the work follows."""
    spec = load_templates(pack)
    planned = list(dict.fromkeys(task_ids))
    sections = []
    for key, template in (spec.get("templates") or {}).items():
        covered = [t for t in planned if t in (template.get("for_tasks") or [])]
        if not covered:
            continue
        if key == "engine_change":
            when = f"at the first free slot {slot}" if slot else f"by {deadline}"
        elif key == "shop_visit_induction":
            when = "on the removed engine, in the shop"
        else:
            when = "at shop induction, on the removed engine" if removal else f"on wing by {deadline}"
        tasks = [WorkTask(**t) for t in template.get("tasks") or []]
        lease = set(covered) & set(template.get("verify_for_tasks") or [])
        verify = (template.get("verify_before_installation") or []) if lease else []
        documents = governed_by if template.get("follows_documents") else {}
        governing = list(dict.fromkeys(documents[t] for t in covered if t in (documents or {})))
        sections.append(WorkSection(
            template=key, title=template["title"], for_tasks=covered, when=when, skill=template.get("skill") or "",
            tasks=tasks, tooling=list(template.get("tooling") or []), manhours=round(sum(t.manhours for t in tasks), 1),
            governed_by=", ".join(governing) or None, verify_before_installation=list(verify)).model_dump(mode="json"))
    if not sections:
        return None
    return {"note": spec.get("note") or "", "sections": sections,
            "manhours": round(sum(s["manhours"] for s in sections), 1),
            "rii": sum(t["rii"] for s in sections for t in s["tasks"]),
            "tasks": sum(len(s["tasks"]) for s in sections),
            "verify_items": sum(len(s["verify_before_installation"]) for s in sections),
            "per_section": [{"template": s["template"], "tasks": len(s["tasks"]), "rii": sum(t["rii"] for t in s["tasks"]),
                             "manhours": s["manhours"]} for s in sections]}


def typed(data: dict[str, Any], evidence_id: str) -> WorkPackage:
    return WorkPackage(sections=[WorkSection(**s) for s in data["sections"]], manhours=data["manhours"],
                       rii=data["rii"], note=data["note"], evidence_ids=[evidence_id])


def summary(data: dict[str, Any]) -> str:
    """One sentence for the plan's claim: each part with its tasks, RII and manhours, then the lease-engine checks."""
    parts = []
    for s, n in zip(data["sections"], data["per_section"]):
        title = s["title"][:1].lower() + s["title"][1:]
        parts.append(f"{title} {s['when']} ({n['tasks']} tasks, {n['rii']} RII, {n['manhours']:g} manhours)")
    text = "; ".join(parts)
    if data["verify_items"]:
        text += f"; before installing the lease engine, verify {data['verify_items']} items Cleris has no data on"
    return text
