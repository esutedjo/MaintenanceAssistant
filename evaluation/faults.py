"""Seeded faulty briefs. Each fault corrupts one finished case file the way a careless or hallucinating agent
might. The eval checks that the deterministic gates (rule checks, then the critic's deterministic checks) stop it.
A fault returns None when it does not apply to the case (no mandate to drop, no short part to inflate)."""

from collections.abc import Callable
from datetime import timedelta

from casefile.schemas import CaseFile, Claim, PartNeed
from models.fault_mode import discriminating

Fault = Callable[[CaseFile, dict], CaseFile | None]


def _copy(case: CaseFile) -> CaseFile:
    return case.model_copy(deep=True)


def uncited_claim(case, pack):
    c = _copy(case)
    c.recommendation.rationale.append(Claim(text="Stock of TF-HPC-1100 is sufficient.", evidence_ids=["EV-MNT-99"],
                                            confidence="high"))
    return c


def evidence_deleted(case, pack):
    c = _copy(case)
    del c.evidence[c.findings.rul_ml.evidence_id]
    return c


def rul_above_cap(case, pack):
    c = _copy(case)
    cap = float(pack["thresholds"]["rul_cap_cycles"])
    c.findings.rul_ml.rul = c.findings.rul_ml.upper = cap + 15
    return c


def rul_outside_interval(case, pack):
    c = _copy(case)
    a = c.findings.rul_ml
    if a.upper >= float(pack["thresholds"]["rul_cap_cycles"]) - 1:
        return None
    a.rul = a.upper + 1
    return c


def rul_inflated(case, pack):
    """The dangerous one: the brief reports a healthier engine than the model said, numbers still in range."""
    c = _copy(case)
    a, cap = c.findings.rul_ml, float(pack["thresholds"]["rul_cap_cycles"])
    if a.upper + 30 > cap:
        return None
    a.rul, a.lower, a.upper = a.rul + 30, a.lower + 30, a.upper + 30
    return c


def disagreement_averaged(case, pack):
    c = _copy(case)
    a, b = c.findings.rul_ml, c.findings.rul_trend
    if b is None or abs(a.rul - b.rul) <= pack["thresholds"]["rul_divergence_cycles"]:
        return None
    mean = round((a.rul + b.rul) / 2, 1)
    a.rul = b.rul = mean
    a.lower, b.lower = min(a.lower, mean), min(b.lower, mean)
    a.upper, b.upper = max(a.upper, mean), max(b.upper, mean)
    return c


def wrong_asset(case, pack):
    c = _copy(case)
    other = "FD003-A100" if c.asset.asset_id != "FD003-A100" else "FD001-A001"
    c.intent = c.intent.model_copy(update={"asset_id": other})
    return c


def fabricated_part(case, pack):
    c = _copy(case)
    cited = next((p.evidence_ids for p in c.recommendation.parts), None) or c.recommendation.rationale[0].evidence_ids
    c.recommendation.parts.append(PartNeed(part_number="TF-HPC-9999", qty=1, available=1, lead_time_days=0,
                                           evidence_ids=list(cited)))
    return c


def stock_inflated(case, pack):
    c = _copy(case)
    short = [p for p in c.recommendation.parts if p.available < p.qty]
    if not short:
        return None
    short[0].available = short[0].qty
    return c


def slot_not_free(case, pack):
    c = _copy(case)
    if c.recommendation.slot is None:
        return None
    c.recommendation.slot = c.recommendation.slot + timedelta(days=365)
    return c


def deadline_past_rul(case, pack):
    c = _copy(case)
    if c.recommendation.deadline is None:
        return None
    c.recommendation.deadline = c.recommendation.deadline + timedelta(days=30)
    return c


def mandate_dropped(case, pack):
    c = _copy(case)
    tasks = {d.required_task for d in c.findings.documents if d.mandatory and d.required_task}
    if not tasks & set(c.recommendation.task_ids):
        return None
    c.recommendation.task_ids = [t for t in c.recommendation.task_ids if t not in tasks]
    return c


def mandate_uncited(case, pack):
    c = _copy(case)
    ids = {d.evidence_id for d in c.findings.documents if d.mandatory}
    if not ids:
        return None
    c.recommendation.rationale = [r for r in c.recommendation.rationale if ids.isdisjoint(r.evidence_ids)]
    if not c.recommendation.rationale:
        return None
    return c


def drivers_as_signature(case, pack):
    c = _copy(case)
    variants = pack["variants"].get(c.asset.variant or "", [])
    if len(variants) < 2 or not c.findings.drivers or not c.findings.fault_modes:
        return None
    disc = set(discriminating(pack, variants).get(c.findings.fault_modes[0].failure_mode, {}))
    if not {d.sensor for d in c.findings.drivers}.isdisjoint(disc):
        return None                                  # drivers already include a distinguishing sensor: no fault
    c.findings.claims = [x for x in c.findings.claims if "distinguish" not in x.text.lower()]
    return c


def similar_cases_dropped(case, pack):
    c = _copy(case)
    if not c.findings.similar_cases:
        return None
    c.findings.similar_cases = []
    return c


FAULTS: dict[str, Fault] = {f.__name__: f for f in (
    uncited_claim, evidence_deleted, rul_above_cap, rul_outside_interval, rul_inflated, disagreement_averaged,
    wrong_asset, fabricated_part, stock_inflated, slot_not_free, deadline_past_rul, mandate_dropped,
    mandate_uncited, drivers_as_signature, similar_cases_dropped,
)}
