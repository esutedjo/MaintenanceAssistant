"""Context-driven follow-ups: after round 1 the planner reviews the findings and delegates extra work only when they
call for it. Each follow-up computes new evidence the plan cites, and can make the plan more cautious, never less."""

import pytest

from casefile.decision_log import derive_log
from evaluation.data_faults import DATA_FAULTS, CorruptSource
from graph.runner import execute_run
from models.fault_mode import SignatureMatcher
from settings import Settings
from tools.data_access import SnapshotSource

DISAGREE = "The two remaining-life estimates for FD001-A070 look different. What should I do?"
FAN = "How long can FD003-A100 keep flying and what is wrong with it?"


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def _follow_ups(case):
    return [e for e in case.trace if e.action == "follow_up"]


@pytest.mark.parametrize("question", ["What is the status of FD001-A001?", "What should I do about FD001-A031?", FAN])
def test_clear_cases_stay_on_the_short_path(settings, question):
    case = execute_run(question, settings).case
    review = next(e for e in case.trace if e.action == "review_findings")
    assert review.output_summary.startswith("no follow-up needed: RUL methods agree within 20 cycles")
    assert review.target_agent == "knowledge" and not _follow_ups(case)
    assert all(t.id.startswith("T-fix-") for t in case.plan if t.kind)       # repairs only, no follow-up


def test_disagreeing_methods_get_inspection_history_and_a_backtest(settings):
    case = execute_run(DISAGREE, settings).case
    delegated = [(e.target_agent, e.output_summary.split(":")[0]) for e in case.trace if e.action == "delegate"]
    assert delegated == [("data", "inspection_history"), ("prognostics", "backtest")]
    assert "differ by 55 cycles (limit 20)" in next(e for e in case.trace if e.action == "delegate").output_summary
    assert [e.agent_name for e in _follow_ups(case)] == ["data", "prognostics"]
    point = next(e for e in case.evidence.values() if "reference_cycle" in e.content and e.produced_by == "data")
    assert point.content["reference_wo"] == "WO-000632" and point.content["reference_cycle"] == 120
    backtest = next(e for e in case.evidence.values() if "optimistic_missed_wear" in e.content)
    assert backtest.id not in {"EV-PRG-01", "EV-PRG-02"} and backtest.content["cycles_since"] == 32
    assert backtest.content["rul_a_then"] > backtest.content["rul_a_now"]
    assert any(backtest.id in c.evidence_ids for c in case.recommendation.rationale)
    rec = case.recommendation
    assert rec.action.startswith("Borescope") and rec.confidence == "low" and str(rec.deadline) == "2026-11-07"
    log = [(e.actor, e.decision) for e in derive_log(case)]
    assert log.count(("planner", "delegated follow-up")) == 2
    assert ("data", "ran follow-up") in log and ("prognostics", "ran follow-up") in log


class _WornAtLastInspection(SnapshotSource):
    """FD001-A070's last borescope (cycle 120) recorded moderate wear instead of none."""

    def history(self, asset_id):
        result = super().history(asset_id)
        frame = result.frame.copy()
        last = frame.index[frame.wo_type == "inspection"][-1]
        frame.loc[last, "severity"] = "moderate"
        result.frame = frame
        return result


def test_backtest_brings_the_inspection_forward_when_the_optimistic_method_missed_wear(settings):
    case = execute_run(DISAGREE, settings, data=_WornAtLastInspection()).case
    backtest = next(e for e in case.evidence.values() if "optimistic_missed_wear" in e.content)
    assert backtest.content["optimistic_method"] == "A" and backtest.content["optimistic_missed_wear"]
    rec = case.recommendation
    assert case.status == "needs_review" and str(rec.deadline) == "2026-10-23"           # first free slot
    assert rec.rationale[0].text.startswith("Deadline 2026-10-23 is the first free slot")
    assert backtest.id in rec.rationale[0].evidence_ids


def _probabilities(fan: float, hpc: float):
    real = SignatureMatcher.match

    def match(self, cycles, variant):
        found = real(self, cycles, variant)
        for item in found:
            item.probabilities = {"fan_degradation": fan, "hpc_degradation": hpc}
        return found
    return match


def test_unclear_fault_mode_checks_both_candidates(settings, monkeypatch):
    monkeypatch.setattr(SignatureMatcher, "match", _probabilities(0.55, 0.45))
    case = execute_run(FAN, settings).case
    assert [e.target_agent for e in case.trace if e.action == "delegate"] == ["diagnostics"]
    check = next(e for e in case.evidence.values() if "runner_up_leads" in e.content)
    assert check.content["candidates"] == ["fan_degradation", "hpc_degradation"]
    assert check.content["vote_counts"]["fan_degradation"] > check.content["vote_counts"]["hpc_degradation"]
    runner_up = [i for i in case.findings.similar_cases if "hpc_degradation" in case.evidence[i].content["tags"]]
    assert runner_up, "past removals for the runner-up were added"
    assert case.recommendation.action.startswith("Inspection targeting both")


def test_sensors_favouring_the_runner_up_make_the_plan_inspect_both(settings, monkeypatch):
    """Probabilities say HPC 0.80 (not within 0.15, so no ambiguity rule), but the discriminating sensors over 30
    cycles follow fan degradation: the planner inspects both modules instead of planning HPC work."""
    monkeypatch.setattr(SignatureMatcher, "match", _probabilities(0.20, 0.80))
    case = execute_run(FAN, settings).case
    check = next(e for e in case.evidence.values() if "runner_up_leads" in e.content)
    assert check.content["runner_up_leads"] and check.content["leader"] == "fan_degradation"
    rec = case.recommendation
    assert case.status == "needs_review" and rec.action.startswith("Inspection targeting both")
    assert rec.confidence == "low" and any(check.id in c.evidence_ids for c in rec.rationale)


def test_implausible_input_is_read_again(settings):
    case = execute_run("What should I do about FD001-A031?", settings,
                       data=CorruptSource("FD001-A031", DATA_FAULTS["spike_t30"])).case
    assert [e.target_agent for e in case.trace if e.action == "delegate"] == ["data"]
    reread = next(e for e in case.evidence.values() if "reproduced" in e.content)
    assert reread.content["reproduced"] and reread.content["same_as_first_read"]
    rec = case.recommendation
    assert rec.action.startswith("Confirm the input data first; then:") and rec.confidence == "low"
    assert rec.rationale[1].evidence_ids == [reread.id] and "bad source data" in rec.rationale[1].text


class _CorruptOnce(CorruptSource):
    """The first telemetry read is corrupt in transit; a re-read is clean."""

    calls = 0

    def cycles(self, asset_id):
        self.calls += 1
        return super().cycles(asset_id) if self.calls == 1 else SnapshotSource.cycles(self, asset_id)


def test_a_clean_reread_says_the_first_read_was_faulty(settings):
    case = execute_run("What should I do about FD001-A031?", settings,
                       data=_CorruptOnce("FD001-A031", DATA_FAULTS["spike_t30"])).case
    reread = next(e for e in case.evidence.values() if "reproduced" in e.content)
    assert not reread.content["reproduced"] and reread.content["data_quality"] == "ok"
    rec = case.recommendation
    assert rec.action.startswith("Confirm the input data first (a re-read was clean") and rec.confidence == "low"
    assert any(e.rule == "data_quality" for e in case.escalations)
