from datetime import date

from assurance.escalation import (
    ambiguous_fault, remaining_shorter_than_wait, rul_below_lead_time, rul_divergence, run_escalations,
)
from assurance.rules import (
    asset_matches, claims_have_evidence, deadline_within_rul, findings_match_evidence, parts_exist, rul_in_range,
    run_rule_checks, slot_exists, world_tasks_eligible,
)
from casefile.schemas import (
    AssetRef, CaseFile, Claim, Escalation, Evidence, FaultModeFinding, Intent, PartNeed, Recommendation, RulFinding,
)
from data.db import load_pack

PACK = load_pack()


def _ev(id_: str, content=None) -> Evidence:
    return Evidence(id=id_, kind="query", source="test", summary="s", content=content or {}, produced_by="data")


def _rul(method="lightgbm", rul=40, lower=30, upper=50, ev="EV-PRG-01") -> RulFinding:
    return RulFinding(method=method, rul=rul, lower=lower, upper=upper, at_cap=False, evidence_id=ev)


def _case(**kwargs) -> CaseFile:
    evidence = kwargs.pop("evidence", {"EV-PRG-01": _ev("EV-PRG-01"), "EV-PRG-02": _ev("EV-PRG-02")})
    base = dict(case_id="c", question="q", asset=AssetRef(asset_id="FD001-A001", asset_type="turbofan_cmapss",
                                                          pack_version="0.1.0", variant="FD001"),
                intent=Intent(goal="full_assessment", asset_id="FD001-A001"), evidence=evidence, round=1)
    base.update(kwargs)
    return CaseFile(**base)


def test_claim_without_resolvable_evidence_fails():
    case = _case(findings={"claims": [Claim(text="x", evidence_ids=["EV-PRG-99"], confidence="high")]})
    assert claims_have_evidence(case)


def test_rul_outside_cap_fails():
    case = _case(findings={"rul_ml": _rul(rul=200, lower=0, upper=200)})
    assert rul_in_range(case, 125)


def test_asset_mismatch_fails():
    case = _case(intent=Intent(goal="status", asset_id="FD003-A100"))
    assert asset_matches(case)


def test_unknown_part_fails():
    rec = Recommendation(action="replace", task_ids=["FM-HPC-04"],
                         parts=[PartNeed(part_number="TF-NOPE", qty=1, available=0, lead_time_days=1,
                                         evidence_ids=["EV-MNT-01"])],
                         rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high")
    case = _case(recommendation=rec, evidence={**{k: _ev(k) for k in ("EV-PRG-01", "EV-PRG-02")},
                                               "EV-MNT-01": _ev("EV-MNT-01", {"unknown": ["TF-NOPE"], "parts": []})})
    assert parts_exist(case)


def test_slot_must_be_in_the_free_list():
    rec = Recommendation(action="inspect", task_ids=["FM-HPC-01"], slot=date(2026, 10, 23),
                         rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high")
    case = _case(recommendation=rec, evidence={"EV-PRG-01": _ev("EV-PRG-01"),
                                               "EV-DAT-04": _ev("EV-DAT-04", {"first_free": "2026-10-24",
                                                                              "next": ["2026-10-24"]})})
    assert slot_exists(case)
    rec.slot = date(2026, 10, 24)
    assert slot_exists(case) == []


def test_task_off_the_world_model_fails_rule_checks():
    rec = Recommendation(action="invent", task_ids=["FM-HPC-99"],
                         rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high")
    case = _case(recommendation=rec, evidence={
        "EV-PRG-01": _ev("EV-PRG-01"),
        "EV-MNT-04": _ev("EV-MNT-04", {"failure_mode": "hpc_degradation", "task_ids": ["FM-HPC-01"]}),
    })
    case.evidence["EV-MNT-04"].source = "sqlite:world/hpc_degradation"
    assert world_tasks_eligible(case)


def test_clean_case_passes_rule_checks():
    rec = Recommendation(action="inspect", task_ids=["FM-HPC-01"], slot=date(2026, 10, 23),
                         rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high")
    case = _case(findings={"rul_ml": _rul(), "rul_trend": _rul("health_index", ev="EV-PRG-02"),
                           "claims": [Claim(text="ok", evidence_ids=["EV-PRG-01"], confidence="high")]},
                 recommendation=rec,
                 evidence={"EV-PRG-01": _ev("EV-PRG-01"), "EV-PRG-02": _ev("EV-PRG-02"),
                           "EV-DAT-04": _ev("EV-DAT-04", {"first_free": "2026-10-23", "next": ["2026-10-23"]})})
    assert run_rule_checks(case, PACK).outcome == "accept"


def _part_case(part: PartNeed) -> CaseFile:
    rec = Recommendation(action="replace", task_ids=["FM-HPC-04"], parts=[part],
                         rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high")
    stock = {"parts": [{"part_number": "TF-HPC-1100", "available": 0, "lead_time_days": 21}], "unknown": []}
    return _case(recommendation=rec, evidence={"EV-PRG-01": _ev("EV-PRG-01"), "EV-MNT-02": _ev("EV-MNT-02", stock)})


def test_part_must_match_the_stock_evidence_it_cites():
    good = PartNeed(part_number="TF-HPC-1100", qty=1, available=0, lead_time_days=21, evidence_ids=["EV-MNT-02"])
    assert parts_exist(_part_case(good)) == []
    inflated = good.model_copy(update={"available": 1})
    assert "differs from stock evidence" in parts_exist(_part_case(inflated))[0]
    invented = good.model_copy(update={"part_number": "TF-HPC-9999"})
    assert "not listed in the evidence" in parts_exist(_part_case(invented))[0]


def test_findings_must_equal_the_model_output_they_cite():
    evidence = {"EV-PRG-01": _ev("EV-PRG-01", {"rul": 40, "lower": 30, "upper": 50}), "EV-PRG-02": _ev("EV-PRG-02")}
    case = _case(findings={"rul_ml": _rul()}, evidence=evidence)
    assert findings_match_evidence(case) == []
    case.findings.rul_ml.rul = 70
    assert "differs from EV-PRG-01" in findings_match_evidence(case)[0]


def test_deadline_may_not_pass_the_rul_lower_bound():
    rec = Recommendation(action="inspect", task_ids=["FM-HPC-01"], deadline=date(2026, 11, 6),
                         rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high")
    case = _case(findings={"rul_ml": _rul(lower=30), "rul_trend": _rul("health_index", lower=40, ev="EV-PRG-02")},
                 recommendation=rec)
    reference = date(2026, 10, 22)
    assert deadline_within_rul(case, PACK, reference) == []          # 30 cycles at 2/day = 15 days
    rec.deadline = date(2026, 11, 7)
    assert "later than the RUL lower bound" in deadline_within_rul(case, PACK, reference)[0]


def test_divergence_escalates_when_gap_exceeds_pack_limit():
    case = _case(findings={"rul_ml": _rul(rul=80), "rul_trend": _rul("health_index", rul=10, lower=5, upper=15,
                                                                      ev="EV-PRG-02")})
    item = rul_divergence(case, PACK)
    assert item and item.rule == "rul_divergence" and "borescope" in item.actions[0].lower()
    close = _case(findings={"rul_ml": _rul(rul=40), "rul_trend": _rul("health_index", rul=35, ev="EV-PRG-02")})
    assert rul_divergence(close, PACK) is None


def test_ambiguous_fault_escalates_within_pack_gap():
    case = _case(findings={"fault_modes": [
        FaultModeFinding(failure_mode="hpc_degradation", probability=0.52, evidence_id="EV-PRG-01"),
        FaultModeFinding(failure_mode="fan_degradation", probability=0.48, evidence_id="EV-PRG-02")]})
    item = ambiguous_fault(case, PACK)
    assert item and item.rule == "ambiguous_fault"
    clear = _case(findings={"fault_modes": [
        FaultModeFinding(failure_mode="fan_degradation", probability=1.0, evidence_id="EV-PRG-01")]})
    assert ambiguous_fault(clear, PACK) is None


def test_remaining_shorter_than_wait():
    assert remaining_shorter_than_wait(3, 35, 1) is True
    assert remaining_shorter_than_wait(40, 35, 1) is False


def _lead_time_case(available: int) -> CaseFile:
    rec = Recommendation(action="replace", task_ids=["FM-HPC-04"],
                         parts=[PartNeed(part_number="TF-HPC-1101", qty=1, available=available, lead_time_days=35,
                                         evidence_ids=["EV-MNT-01"])],
                         rationale=[Claim(text="r", evidence_ids=["EV-PRG-01"], confidence="high")], confidence="high")
    return _case(findings={"rul_ml": _rul(rul=10, lower=7, upper=11)},
                 recommendation=rec,
                 evidence={"EV-PRG-01": _ev("EV-PRG-01"), "EV-PRG-02": _ev("EV-PRG-02"),
                           "EV-MNT-01": _ev("EV-MNT-01"),
                           "EV-DAT-04": _ev("EV-DAT-04", {"days_to_first_free": 1, "first_free": "2026-10-23"})})


def test_rul_below_lead_time_is_urgent():
    case = _lead_time_case(available=0)
    item = rul_below_lead_time(case, PACK)
    assert item and item.severity == "urgent"
    assert run_escalations(case, PACK)


def test_parts_on_hand_do_not_count_lead_time():
    assert rul_below_lead_time(_lead_time_case(available=2), PACK) is None
