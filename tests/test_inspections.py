"""Inspection findings close the loop an Inspect verdict opens: the finding is stored append-only per engine, the data
agent records it (EV-INS), Knowledge cites the limit section in effect and code compares the finding with it,
diagnostics weighs it against the fault mode, and the maintenance planner does what the manual requires. The rule
checks refuse a plan that ignores a finding or does less than the manual asks."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import run
from agents.maint_planner import REMOVE_ACTION
from agents.critic import deterministic_gaps
from assurance.routing import routes_for
from assurance.rules import inspections_acted_on, run_rule_checks
from casefile.decision_log import derive_log
from casefile.schemas import InspectionFinding, Measurement
from data.db import load_pack
from evaluation.faults import FAULTS
from graph.runner import execute_run
from settings import Settings, get_settings
from tools.data_access import SnapshotSource
from tools.inspections import (
    assess, classify, example_finding, find_row, limit_catalog, limit_section, load_findings, mark_current,
    pack_limit_chunks, parse_limits, record_finding, store_path,
)

PACK = load_pack()
ON = date(2026, 10, 22)
A070 = "The two remaining-life estimates for FD001-A070 look different. What should I do?"
HEALTHY = "What is the status of FD001-A001?"


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def _finding(**kwargs) -> InspectionFinding:
    base = dict(asset_id="FD001-A070", kind="borescope", module="hpc", location="HPC stage 3",
                observation="Tip erosion on stage 3 blades.", inspector="J. Inspector", inspected_on=ON, cycle=152,
                measurement=Measurement(parameter="HPC-TIP", value=0.42, unit="mm"))
    base.update(kwargs)
    return InspectionFinding(**base)


def _run(settings, *findings, question=A070):
    return execute_run(question, settings, inspections=list(findings)).case


# --- Limits and classification ---------------------------------------------------------------------------------

def test_the_limit_table_is_read_from_the_manual_and_code_classifies_against_it():
    table = limit_catalog(PACK, ON, "FD001")[("borescope", "hpc")]
    assert table["reference"] == "AMM-TF-R3 72-31-00-601"
    assert (table["routine_cycles"], table["reinspect_cycles"]) == (60, 30)
    row = find_row(table["rows"], "blade tip erosion")
    assert row == find_row(table["rows"], "hpc-tip")
    assert (row["serviceable"], row["reject"], row["unit"]) == (0.5, 0.75, "mm")
    assert [classify(v, row) for v in (0.42, 0.50, 0.62, 0.75, 0.82)] == ["within", "within", "at_limit", "at_limit",
                                                                           "beyond"]
    assert {kind for kind, _ in limit_catalog(PACK, ON, "FD003")} == {"borescope", "visual", "oil_analysis",
                                                                       "chip_detector", "material_test"}


def test_only_the_revision_in_effect_sets_the_limit():
    chunks = pack_limit_chunks(PACK)
    now, reasons = limit_section(chunks, PACK, "borescope", "hpc", ON, "FD001")
    assert now["doc_id"] == "AMM-TF-R3" and any("superseded" in r["reason"] for r in reasons)
    before, _ = limit_section(chunks, PACK, "borescope", "hpc", date(2026, 1, 15), "FD001")
    assert before["doc_id"] == "AMM-TF-R2"            # revision 3 not yet effective: the looser revision 2 limits
    row = find_row(parse_limits(before["text"])["rows"], "HPC-TIP")
    assert classify(0.82, row) == "at_limit"          # beyond under revision 3, at the limit under revision 2
    assert limit_section(chunks, PACK, "visual", "combustor", ON, "FD001")[0] is None


def test_without_a_matching_limit_the_inspectors_result_stands_and_says_why():
    chunks = pack_limit_chunks(PACK)
    section, _ = limit_section(chunks, PACK, "borescope", "hpc", ON, "FD001")
    table = parse_limits(section["text"])
    kwargs = dict(limit_evidence_id="EV-KNW-09", section=section, limits=table, pack=PACK, fresh=True)
    wrong_unit = _finding(measurement=Measurement(parameter="HPC-TIP", value=20, unit="thou"), stated_result="within")
    a = assess(wrong_unit, "EV-INS-01", **kwargs)
    assert (a.result, a.classified_by) == ("within", "inspector") and "not compared" in a.note
    disagrees = assess(_finding(measurement=Measurement(parameter="HPC-TIP", value=0.9, unit="mm"),
                                stated_result="within"), "EV-INS-01", **kwargs)
    assert (disagrees.result, disagrees.classified_by, disagrees.action) == ("beyond", "limit", "remove")
    assert "the inspector stated within limits" in disagrees.note
    with pytest.raises(ValueError, match="stated no result"):
        assess(_finding(measurement=Measurement(parameter="HPC-SHAPE", value=1, unit="mm")), "EV-INS-01", **kwargs)
    with pytest.raises(ValueError, match="needs a measurement or the inspector's result"):
        _finding(measurement=None)


def test_a_later_inspection_replaces_an_earlier_one_but_a_second_entry_cannot_soften_a_finding():
    chunks = pack_limit_chunks(PACK)
    section, _ = limit_section(chunks, PACK, "borescope", "hpc", ON, "FD001")
    table = parse_limits(section["text"])

    def pair(n, value, cycle):
        f = _finding(measurement=Measurement(parameter="HPC-TIP", value=value, unit="mm"), cycle=cycle)
        return assess(f, f"EV-INS-0{n}", limit_evidence_id="EV-KNW-09", section=section, limits=table, pack=PACK,
                      fresh=True), f

    same_cycle = mark_current([pair(1, 0.82, 152), pair(2, 0.42, 152)])
    assert [(a.evidence_id, a.current, a.superseded_by) for a in same_cycle] == [
        ("EV-INS-01", True, None), ("EV-INS-02", False, "EV-INS-01")]
    later = mark_current([pair(1, 0.82, 140), pair(2, 0.42, 152)])
    assert [a.current for a in later] == [False, True]


# --- Store ---------------------------------------------------------------------------------------------------

def test_findings_are_stored_append_only_per_engine(settings):
    first, second = _finding(), _finding(cycle=150)
    path = record_finding(settings, first)
    before = path.read_bytes()
    record_finding(settings, second)
    assert path == store_path(settings, "FD001-A070") and path.read_bytes().startswith(before)
    assert [f.finding_id for f in load_findings(settings, "FD001-A070")] == [first.finding_id, second.finding_id]
    with pytest.raises(ValueError, match="already recorded"):
        record_finding(settings, first)
    assert load_findings(settings, "FD001-A031") == []


def test_later_runs_read_the_engines_stored_findings(settings):
    record_finding(settings, example_finding("beyond", "FD001-A070", 152, ON))
    case = execute_run(A070, settings).case
    record = next(e for e in case.evidence.values() if e.kind == "inspection")
    assert record.id == "EV-INS-01" and record.source == "entered by Example inspector" and record.content["stored"]
    assert case.recommendation.action.startswith(REMOVE_ACTION)
    other = execute_run("What should I do about FD001-A031?", settings).case
    assert not any(e.kind == "inspection" for e in other.evidence.values())


# --- The loop through the agents ------------------------------------------------------------------------------

def test_a_fresh_finding_within_limits_answers_the_disagreement(settings):
    case = _run(settings, example_finding("within", "FD001-A070", 152, ON))
    delegated = [e.output_summary.split(":")[0] for e in case.trace if e.action == "delegate"]
    assert delegated == ["inspection_check"], "the finding replaces the back-test and the inspection history"
    order = [(e.agent_name, e.action) for e in case.trace]
    assert order.index(("knowledge", "retrieve_documents")) < order.index(("diagnostics", "follow_up"))
    assert order.index(("diagnostics", "follow_up")) < order.index(("maint_planner", "propose_work"))
    [item] = case.findings.inspections
    assert (item.result, item.classified_by, item.reference, item.fresh) == ("within", "limit",
                                                                             "AMM-TF-R3 72-31-00-601", True)
    limit = case.evidence[item.limit_evidence_id]
    assert limit.kind == "document" and limit.content["limits"][0]["id"] == "HPC-TIP"
    assert any(d.section == "72-31-00-601" for d in case.findings.documents)
    check = next(e for e in case.evidence.values() if e.content.get("inspection") == item.evidence_id)
    assert check.produced_by == "diagnostics" and check.content["effect"] == "weakens"
    rec = case.recommendation
    assert rec.action.startswith("No removal") and rec.confidence == "medium" and str(rec.deadline) == "2026-11-07"
    assert rec.task_ids == ["FM-HPC-01"] and any(item.evidence_id in c.evidence_ids for c in rec.rationale)
    divergence = next(e for e in case.escalations if e.rule == "rul_divergence")
    assert divergence.severity == "info" and "answers it" in divergence.message
    assert case.status == "needs_review" and case.verdicts[-1].outcome == "accept"
    log = derive_log(case)
    assert (log[0].actor_type, log[0].actor, log[0].decision) == ("human", "Example inspector",
                                                                  "entered inspection finding")
    assert log[0].rationale.startswith("Example data:") and log[0].evidence_ids == [item.evidence_id]
    assert ("knowledge", "compared finding with limits") in {(e.actor, e.decision) for e in log}


def test_a_finding_beyond_limits_removes_the_engine_before_further_flight(settings):
    case = _run(settings, example_finding("beyond", "FD001-A070", 152, ON))
    rec = case.recommendation
    assert rec.action.startswith(f"{REMOVE_ACTION} (borescope, HPC stage 3 beyond limits)")
    assert rec.deadline == ON and rec.confidence == "high" and {"FM-HPC-04", "FM-HPC-06"} <= set(rec.task_ids)
    assert rec.rationale[0].text.startswith("Deadline 2026-10-22 (the reference date): inspection finding EV-INS-01")
    rules = [(e.rule, e.severity) for e in case.escalations]
    assert rules[0] == ("inspection_beyond_limits", "urgent") and ("potentially_reportable", "info") in rules
    note = next(e for e in case.escalations if e.rule == "potentially_reportable").message
    assert "EU 376/2014 and 2015/1018 Annex II; 14 CFR 121.703 / 145.221" in note and "stays with people" in note
    check = next(e for e in case.evidence.values() if e.content.get("effect"))
    assert check.content["effect"] == "corroborates"
    assert not run_rule_checks(case, PACK, ON).gaps


def test_a_finding_at_the_limit_reinspects_within_the_manual_interval(settings):
    case = _run(settings, _finding(measurement=Measurement(parameter="HPC-TIP", value=0.62, unit="mm")))
    rec = case.recommendation
    assert rec.action.startswith("Re-inspect within 30 cycles (borescope, HPC stage 3 at the limit)")
    assert rec.deadline == ON + timedelta(days=15) and rec.task_ids == ["FM-HPC-03"]
    assert [(e.rule, e.severity) for e in case.escalations][0] == ("inspection_at_limit", "warning")
    assert case.status == "needs_review"


def test_a_finding_on_a_module_no_fault_mode_wears_still_drives_the_plan(settings):
    oil = InspectionFinding(asset_id="FD001-A001", kind="oil_analysis", module="oil_system", location="",
                            observation="Iron trending up over three samples.", inspector="Lab", inspected_on=ON,
                            cycle=31, measurement=Measurement(parameter="OIL-FE", value=9.5, unit="ppm"))
    case = _run(settings, oil, question=HEALTHY)
    rec = case.recommendation
    assert rec.action.startswith(f"{REMOVE_ACTION} (oil analysis, oil system beyond limits)")
    assert not {"FM-HPC-04", "FM-HPC-05"} & set(rec.task_ids), "no HPC blade work for an oil-system finding"
    check = next(e for e in case.evidence.values() if e.content.get("effect"))
    assert check.content["effect"] == "neutral" and case.status == "needs_review"


def test_a_finding_for_another_engine_is_ignored_with_a_note(settings):
    case = _run(settings, _finding(asset_id="FD001-A031"))
    assert not any(e.kind == "inspection" for e in case.evidence.values())
    assert any("names FD001-A031, not FD001-A070; ignored" in d for d in case.degraded)


def test_limits_come_from_the_snapshot_when_the_live_table_predates_them(settings):
    class OldTable(SnapshotSource):
        def doc_chunks(self, asset_type):
            result = super().doc_chunks(asset_type)
            result.frame = result.frame[~result.frame.topic.str.startswith("limits:")]
            return result

    case = execute_run(A070, settings, data=OldTable(),
                       inspections=[example_finding("within", "FD001-A070", 152, ON)]).case
    assert case.findings.inspections[0].classified_by == "limit"
    assert any("limit sections are not in snapshot:doc_chunks.parquet" in d for d in case.degraded)


# --- Gates ------------------------------------------------------------------------------------------------------

def test_the_rule_checks_refuse_a_plan_that_ignores_or_softens_a_finding(settings):
    case = _run(settings, example_finding("beyond", "FD001-A070", 152, ON))
    ignored = FAULTS["inspection_ignored"](case, PACK)          # the finding's citations stripped from the plan
    assert any("is not cited by the plan" in g for g in inspections_acted_on(ignored, PACK, ON))
    later = case.model_copy(deep=True)
    later.recommendation.deadline = ON + timedelta(days=3)
    later.recommendation.action = "Borescope inspection; do not average the two remaining-life estimates"
    later.recommendation.task_ids = ["FM-HPC-01"]
    gaps = inspections_acted_on(later, PACK, ON)
    assert any("the manual requires removal before further flight" in g for g in gaps)
    assert any("the plan does not remove the engine" in g for g in gaps)
    assert {r.kind for r in routes_for(gaps)} == {"inspection_not_acted_on"}
    softened = case.model_copy(deep=True)
    softened.findings.inspections[0].result = "within"
    gap = next(g for g in inspections_acted_on(softened, PACK, ON) if "is reported within" in g)
    assert "0.82 mm against EV-KNW" in gap and routes_for([gap])[0].target is None


def test_the_seeded_fault_injects_a_finding_the_old_plan_ignores(settings):
    for question in (HEALTHY, "What should I do about FD001-A031?", A070):
        case = execute_run(question, settings).case
        faulty = FAULTS["inspection_ignored"](case, PACK)
        assert faulty.findings.inspections[0].result == "beyond"
        gaps = [*run_rule_checks(faulty, PACK, ON).gaps, *deterministic_gaps(faulty, PACK)]
        assert any(g.startswith("inspection finding") for g in gaps)
        assert {r.target for r in routes_for(gaps) if r.kind.startswith("inspection")} == {"maint_planner"}


# --- API --------------------------------------------------------------------------------------------------------

def test_the_api_applies_findings_and_records_them_only_when_the_server_allows(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CONTEXT_SQLITE_PATH", str(tmp_path / "context.sqlite"))
    get_settings.cache_clear()
    finding = {**example_finding("beyond", "FD001-A070", 152, ON).model_dump(mode="json"),
               "finding_id": "INS-client", "photo": "C:/somewhere/else.png", "entered_at": "2099-01-01T00:00:00"}
    status, body = run.handle({"question": A070, "inspections": [finding]})
    assert status == 200 and body["recommendation"]["action"].startswith(REMOVE_ACTION)
    assert "inspections_recorded" not in body and not (tmp_path / "inspections").exists()
    other = {**finding, "asset_id": "FD001-A031"}
    status, body = run.handle({"question": A070, "inspections": [finding, other], "record": True})
    assert status == 403 and body["error"]["code"] == "record_disabled", "recording is off by default"
    assert not (tmp_path / "inspections").exists()
    monkeypatch.setenv("API_ALLOW_RECORD", "true")
    get_settings.cache_clear()
    status, body = run.handle({"question": A070, "inspections": [finding, other], "record": True})
    recorded = body["inspections_recorded"]
    assert [r["recorded"] for r in recorded] == [True, False] and recorded[1]["reason"] == "not about FD001-A070"
    [stored] = load_findings(get_settings(), "FD001-A070")
    assert stored.finding_id == recorded[0]["finding_id"] != "INS-client", "the server assigns the ID"
    assert stored.photo is None and not stored.example and stored.entered_at.year == datetime.now(timezone.utc).year
    status, body = run.handle({"question": A070, "inspections": [{**finding, "measurement": None}]})
    assert status == 400 and "measurement or the inspector's result" in body["error"]["message"]
    status, body = run.handle({"question": A070, "inspections": [{**finding, "observation": "x" * 2001}]})
    assert status == 400 and body["error"]["message"].startswith("inspections.0.observation")
    status, body = run.handle({"question": A070, "inspections": [{**finding, "module": "galley"}]})
    assert status == 400 and "unknown module galley" in body["error"]["message"]


# --- Implausible findings (held back) and time zones ----------------------------------------------------------

def test_a_future_cycle_finding_is_held_back_and_cannot_soften_a_real_one(settings):
    beyond = _finding(measurement=Measurement(parameter="HPC-TIP", value=0.82, unit="mm"))
    record_finding(settings, beyond)
    typo = _finding(cycle=9800, measurement=Measurement(parameter="HPC-TIP", value=0.10, unit="mm"))
    future = _finding(inspected_on=date(2099, 1, 1), measurement=Measurement(parameter="HPC-TIP", value=0.1,
                                                                             unit="mm"))
    case = _run(settings, typo, future)
    assert case.recommendation.action.startswith(f"Confirm the input data first; then: {REMOVE_ACTION}")
    [current] = [a for a in case.findings.inspections if a.current]
    assert current.result == "beyond" and len(case.findings.inspections) == 1, "held-back findings are not assessed"
    held = [e for e in case.evidence.values() if e.kind == "inspection" and e.content["quarantined"]]
    assert len(held) == 2 and not any(e.content["fresh"] for e in held)
    quality = next(e for e in case.escalations if e.rule == "data_quality")
    assert "is after the last telemetry cycle" in quality.message and "after the reference date" in quality.message
    assert case.recommendation.confidence == "low"


def test_freshness_needs_a_cycle_at_or_before_the_last_one(settings):
    case = _run(settings, _finding(cycle=150))
    [record] = [e for e in case.evidence.values() if e.kind == "inspection"]
    assert record.content["fresh"] and not record.content["quarantined"]


def test_a_naive_stored_entry_time_no_longer_breaks_the_engines_brief(settings):
    path = store_path(settings, "FD001-A070")
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = _finding().model_dump(mode="json") | {"finding_id": "INS-legacy", "entered_at": "2026-10-21T08:00:00"}
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    [stored] = load_findings(settings, "FD001-A070")
    assert stored.entered_at.tzinfo is not None and stored.entered_at.utcoffset().total_seconds() == 0
    offset = _finding(entered_at=datetime(2026, 10, 21, 13, 0, tzinfo=timezone(timedelta(hours=5))))
    assert offset.entered_at.hour == 8 and offset.entered_at.utcoffset().total_seconds() == 0
    case = _run(settings, _finding(), offset)
    assert case.status == "needs_review" and len(case.findings.inspections) == 3
