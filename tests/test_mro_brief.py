"""What an engine MRO engineer asked of the brief: documents grouped by what they mean for compliance, the AD's method
of compliance on the plan, an executable work package, and what the shop found on similar past removals."""

import json
from datetime import date

import pytest

from agents.maint_planner import _compliance
from assurance.rules import run_rule_checks
from casefile.schemas import DocumentFinding
from casefile.shop_findings import findings_claim, readable, shop_finding
from data.db import load_pack
from graph.runner import execute_run
from settings import ROOT, Settings
from tools.knowledge import mandate_unmet
from tools.writeback import _record
from ui.brief import document_groups, similar_case_summaries
from ui.help import plain_summary

PACK = load_pack()
ON = date(2026, 10, 22)
AD = {"failure_modes": ["hpc_degradation"], "min_probability": 0.5, "rul_lower_below": 30, "task": "FM-HPC-01",
      "within_cycles": 10, "action": "Borescope"}
QUESTIONS = {"FD001-A001": "What is the status of FD001-A001?", "FD001-A031": "What should I do about FD001-A031?",
             "FD001-A070": "The two remaining-life estimates for FD001-A070 look different. What should I do?",
             "FD003-A100": "What should I do about FD003-A100?"}
NOTE = ("Past case FD001-H011 (FD001, synthetic): HPC degradation. Engine removed unscheduled at cycle 240; first "
        "moderate finding at cycle 240, warning_to_removal_cycles=0. Shop finding: ENGINE REMOVED UNSCHEDULED AT 240 CYC "
        "FOR PERFORMANCE DETERIORATION, EGT MARGIN EXHAUSTED. SHOP FOUND HPC STG 1-3 BLADES ERODED, TIP CLEARANCE BEYOND "
        "LIMITS. REPLACED TF-HPC-1101, TF-HPC-1104, TF-GEN-8102.")


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    settings = Settings(_env_file=None, sample_mode=True,
                        context_sqlite_path=tmp_path_factory.mktemp("ctx") / "context.sqlite")
    return settings, {a: execute_run(q, settings).case for a, q in QUESTIONS.items()}


def _claims(case):
    return [c.text for c in [*case.findings.claims, *case.recommendation.rationale]]


def test_unmet_mandate_conditions_are_worded_for_the_engineer():
    assert mandate_unmet(AD, [("hpc_degradation", 1.0)], 115.0) == "RUL lower bound 115 ≥ 30 cycles"
    assert mandate_unmet(AD, [("hpc_degradation", 0.3)], 5.0) == "hpc_degradation p=0.30 < 0.5"
    assert mandate_unmet(AD, [("fan_degradation", 1.0)], 40.0) == ("no hpc_degradation finding and RUL lower bound "
                                                                  "40 ≥ 30 cycles")


def test_healthy_engine_lists_the_ad_as_reference_with_the_reason(cases):
    _, by_id = cases
    case = by_id["FD001-A001"]
    groups = {d.doc_id + " " + d.section: (d.group, d.group_reason) for d in case.findings.documents}
    assert groups["AD-2026-17-04 (g)"] == ("reference", "conditions not met: RUL lower bound 115 ≥ 30 cycles")
    assert groups["SB-TF-72-0142 3.B"][0] == "recommended"
    assert groups["AMM-TF-R3 72-31-00"][0] == groups["AMM-TF-R3 05-20-00"][0] == "reference"
    claims = _claims(case)
    assert not any(c.startswith(("Applicable documents", "Mandatory now")) for c in claims)
    assert any(c.startswith("Reference: AD-2026-17-04 (g) (airworthiness directive, conditions not met: RUL lower "
                            "bound 115 ≥ 30 cycles)") for c in claims)
    assert case.recommendation.compliance == [] and case.recommendation.work_package is None
    shown = [(key, title, [d["doc_id"] for d in docs]) for key, title, docs in document_groups(case.model_dump())]
    assert [s[1] for s in shown] == ["Recommended, needs engineering approval", "Reference"]


def test_swapped_engine_states_how_the_ad_is_complied_with(cases):
    _, by_id = cases
    case = by_id["FD001-A031"]
    ad = next(d for d in case.findings.documents if d.doc_id == "AD-2026-17-04")
    assert ad.group == "mandatory" and ad.group_reason.startswith("conditions hold: hpc_degradation p=1.00")
    line = case.recommendation.compliance[0]
    assert (line.method, line.comply_by, line.limit, line.task_id) == ("engine_removal", date(2026, 10, 23),
                                                                        date(2026, 10, 27), "FM-HPC-01")
    assert line.text == ("AD-2026-17-04 (g): complied with by engine removal before the 10-cycle limit (removal at "
                         "the first free slot 2026-10-23, limit 2026-10-27); FM-HPC-01 (borescope inspection HPC "
                         "stages 1-9) at shop induction; record in the AD compliance record")
    assert f"{line.text}." in _claims(case) and ad.evidence_id in line.evidence_ids
    record = _record(case, "approved", "")
    assert record["compliance"][0]["text"] == line.text and record["work_package"]["manhours"] == 58.5
    assert "complies by engine removal before the 10-cycle limit" in " ".join(plain_summary(case.model_dump(), PACK))


def test_unadopted_bulletin_without_a_shared_topic_still_leaves_the_work_on_the_manual(cases):
    # SB-TF-72-0157 3.B and AMM 72-21-00 both cover FM-FAN-03 but share no topic, so precedence replaces nothing; the
    # bulletin is still not approved data until engineering adopts it.
    _, by_id = cases
    case = by_id["FD003-A100"]
    assert "Limits and intervals for FM-FAN-03 follow AMM-TF-R3 72-21-00 (manual); SB-TF-72-0157 3.B (service " \
           "bulletin) also covers it: SB recommended; adopting it needs engineering approval." in _claims(case)
    note = next(e for e in case.escalations if e.rule == "sb_adoption" and "SB-TF-72-0157" in e.message)
    assert note.actions[-1] == "Until it is approved, the plan follows AMM-TF-R3 72-21-00 for FM-FAN-03"
    shop = next(s for s in case.recommendation.work_package.sections if s.template == "shop_visit_induction")
    assert shop.governed_by == "AMM-TF-R3 72-21-00"


def test_on_wing_compliance_line_names_the_task_and_date():
    doc = DocumentFinding(doc_id="AD-2026-17-04", doc_type="airworthiness_directive", section="(g)", heading="h",
                          revision="0", effective_date="2026-09-01", precedence=0, relevance=0.2, mandatory=True,
                          required_task="FM-HPC-01", within_cycles=10, action="Borescope", evidence_id="EV-KNW-02")
    task = {"fmea_id": "FM-HPC-01", "task": "Borescope inspection HPC stages 1-9; reinspect at standard interval"}
    line = _compliance(doc, [task], False, date(2026, 10, 25), False, ON, 2, ["EV-MNT-04"])
    assert line.text == ("AD-2026-17-04 (g): complied with by FM-HPC-01 (borescope inspection HPC stages 1-9) by "
                         "2026-10-25 (within 10 cycles, limit 2026-10-27); record in the AD compliance record")
    assert line.method == "on_wing" and line.evidence_ids == ["EV-KNW-02", "EV-MNT-04"]


def test_lease_engine_swap_comes_with_an_executable_work_package(cases):
    _, by_id = cases
    package = by_id["FD001-A031"].recommendation.work_package
    sections = {s.template: s for s in package.sections}
    assert list(sections) == ["engine_change", "shop_visit_induction", "borescope_inspection"]
    change = sections["engine_change"]
    assert change.when == "at the first free slot 2026-10-23" and "B1 type-rated certifying staff" in change.skill
    rii = [t.title for t in change.tasks if t.rii]
    assert len(rii) == 3 and any("mounts" in t for t in rii) and any("control" in t for t in rii) \
        and any("Fuel" in t for t in rii)
    assert any("ground run" in t.title for t in change.tasks) and any("Leak check" in t.title for t in change.tasks) \
        and any("Vibration check" in t.title for t in change.tasks)
    assert {"Engine sling and hoist (bootstrap kit)", "Engine transport stands: one for each engine"} <= set(change.tooling)
    verify = " ".join(change.verify_before_installation)
    for item in ("thrust rating", "QEC", "EASA Form 1", "FAA 8130-3", "Life-limited parts", "Transport"):
        assert item in verify, item
    assert sections["borescope_inspection"].when == "at shop induction, on the removed engine"
    assert all(t.ref.startswith("SYN-") for s in package.sections for t in s.tasks), "references are synthetic"
    assert "SYNTHETIC" in package.note and package.rii == 4 and package.manhours == 58.5
    assert any(c.startswith("Work package (asset-pack templates, synthetic task references): engine change at the "
                            "first free slot 2026-10-23 (9 tasks, 3 RII, 30 manhours)") for c in _claims(by_id["FD001-A031"]))


def test_inspection_plan_gets_the_on_wing_borescope_package(cases):
    _, by_id = cases
    package = by_id["FD001-A070"].recommendation.work_package
    assert [(s.template, s.when, s.governed_by) for s in package.sections] == [
        ("borescope_inspection", "on wing by 2026-11-07", "AMM-TF-R3 72-31-00")]
    assert not package.sections[0].verify_before_installation


def test_shop_findings_are_read_from_the_case_record_in_sentence_case():
    found = shop_finding(NOTE, PACK)
    assert found == {"case": "FD001-H011", "removed_at_cycle": 240, "warning_cycles": 0,
                     "reason": "performance deterioration, EGT margin exhausted",
                     "found": "HPC stage 1-3 blades eroded, tip clearance beyond limits",
                     "replaced": ["TF-HPC-1101", "TF-HPC-1104", "TF-GEN-8102"]}
    assert readable("FAN BLADE LEADING EDGES ERODED AND CASE LINER RUB", PACK) == ("fan blade leading edges eroded and "
                                                                                   "case liner rub")
    assert shop_finding("Engineer approved FD001-A031", PACK) is None
    two = findings_claim([found, {**found, "case": "FD001-H052", "removed_at_cycle": 209}])
    assert two == ("2 similar past removals in case memory (FD001-H011 at cycle 240, FD001-H052 at cycle 209); the "
                   "shop found HPC stage 1-3 blades eroded, tip clearance beyond limits on both, after removal for "
                   "performance deterioration, EGT margin exhausted.")


def test_brief_says_what_the_shop_found_and_its_numbers_check_out(cases):
    settings, by_id = cases
    for asset in ("FD001-A031", "FD001-A070"):
        case = by_id[asset]
        claim = next(c for c in case.findings.claims if "similar past removals in case memory" in c.text)
        assert "the shop found HPC stage 1-3 blades eroded, tip clearance beyond limits" in claim.text
        assert claim.evidence_ids == case.findings.similar_cases
        assert run_rule_checks(case, PACK, settings.reference_time.date()).gaps == []       # numbers_supported too
        lines = similar_case_summaries(case.model_dump())
        assert len(lines) == 3 and all("the shop found HPC stage 1-3 blades eroded" in line for line in lines)
        assert "replaced TF-HPC-1101" in lines[0]
        text = " ".join(plain_summary(case.model_dump(), PACK))
        assert "3 similar past engines had the same problem, and the shop found HPC stage 1-3 blades eroded" in text


def test_committed_examples_show_the_groups_the_compliance_line_and_the_package():
    hpc = json.loads((ROOT / "output_examples" / "critical_hpc.json").read_text(encoding="utf-8"))
    assert hpc["recommendation"]["compliance"][0]["text"].startswith("AD-2026-17-04 (g): complied with by engine "
                                                                    "removal before the 10-cycle limit")
    assert hpc["recommendation"]["work_package"]["sections"][0]["template"] == "engine_change"
    healthy = json.loads((ROOT / "output_examples" / "healthy_engine.json").read_text(encoding="utf-8"))
    ad = next(d for d in healthy["findings"]["documents"] if d["doc_id"] == "AD-2026-17-04")
    assert (ad["group"], ad["group_reason"]) == ("reference", "conditions not met: RUL lower bound 115 ≥ 30 cycles")
