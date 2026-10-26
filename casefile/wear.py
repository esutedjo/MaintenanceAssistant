"""No wear detected on a fleet with one known fault mode. The signature matcher gives that mode probability 1.00 by
construction, so on a healthy engine "HPC degradation (probability 1.00)" would read as a diagnosis. The brief, the
plan and the UI say instead that no wear is detected and name the fleet's only known wear mode. Degraded engines, and
fleets with two or more modes, keep the fault-mode wording."""

from __future__ import annotations

from typing import Any

NO_WEAR = "No wear detected; this fleet's only known wear mode is {label}"
NOT_DETECTED = "none detected"


def only_mode(pack: dict, variant: str | None) -> str | None:
    modes = (pack.get("variants") or {}).get(variant or "", [])
    return modes[0] if len(modes) == 1 else None


def no_wear(findings: dict[str, Any], pack: dict) -> bool:
    """Both remaining-life methods agree within the pack's divergence limit and one of them is at the cap (the
    planner's healthy band), the health index is not flagged against the fleet, and no current inspection finding is
    at or beyond its limit."""
    a, b = findings.get("rul_ml"), findings.get("rul_trend")
    if not a or not b:
        return False
    limit = pack["thresholds"]["rul_divergence_cycles"]
    if not (a.get("at_cap") or b.get("at_cap")) or abs(a["rul"] - b["rul"]) > limit:
        return False
    if (findings.get("anomaly") or {}).get("flagged"):
        return False
    return not any(i.get("current", True) and i.get("result") in ("at_limit", "beyond")
                   for i in findings.get("inspections") or [])


def no_wear_label(findings: dict[str, Any], pack: dict, variant: str | None) -> str | None:
    """The fleet's only known wear mode (its label) when this engine shows no wear, else None."""
    mode = only_mode(pack, variant)
    if mode is None or not no_wear(findings, pack):
        return None
    return (pack.get("failure_modes") or {}).get(mode, {}).get("label", mode.replace("_", " "))


def no_wear_text(label: str) -> str:
    return NO_WEAR.format(label=label)
