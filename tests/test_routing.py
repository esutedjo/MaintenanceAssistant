"""Gap-routed revise loop: every gap a gate raises has a kind and an owner, the planner routes it there, the owner's
repair is what makes the brief pass, and a repair that finds nothing new leaves the gap open until the round limit."""

import pytest

import agents.maint_planner as maint_planner
import assurance.rules
from agents.critic import deterministic_gaps
from assurance import routing
from assurance.rules import run_rule_checks
from assurance.routing import ROUTES, gap_kind, named_parts, routes_for
from casefile.calls import CALL_ACTIONS
from casefile.context_store import SqliteStore
from casefile.decision_log import derive_log
from data.db import load_pack
from evaluation.faults import FAULTS
from graph.runner import execute_run
from settings import Settings
from tools.data_access import SnapshotSource

PACK = load_pack()
HPC = "What should I do about FD001-A031?"
GOLDEN = {"FD001-A031": HPC, "FD003-A100": "How long can FD003-A100 keep flying and what is wrong with it?",
          "FD001-A070": "The two remaining-life estimates for FD001-A070 look different. What should I do?"}
# The agent that owns each seeded fault's gap (the fault may raise other gaps too).
OWNER = {"uncited_claim": "maint_planner", "evidence_deleted": "prognostics", "rul_above_cap": "prognostics",
         "rul_outside_interval": "prognostics", "rul_inflated": "prognostics", "disagreement_averaged": "prognostics",
         "wrong_asset": None, "fabricated_part": "data", "stock_inflated": "data", "slot_not_free": "data",
         "deadline_past_rul": "maint_planner", "mandate_dropped": "knowledge", "mandate_uncited": "knowledge",
         "drivers_as_signature": "maint_planner", "similar_cases_dropped": "diagnostics",
         "claim_number_altered": "maint_planner", "inspection_ignored": "maint_planner"}


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    settings = Settings(_env_file=None, sample_mode=True,
                        context_sqlite_path=tmp_path_factory.mktemp("ctx") / "context.sqlite")
    return settings, {a: execute_run(q, settings).case for a, q in GOLDEN.items()}


def _events(case, agent=None, action=None):
    """Agent steps (Zetaris and context-store call lines left out)."""
    return [e for e in case.trace if (agent is None or e.agent_name == agent) and (action is None or e.action == action)
            and e.action not in CALL_ACTIONS]


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_every_seeded_gap_has_a_kind_and_reaches_its_owner(golden, name):
    settings, cases = golden
    assert set(OWNER) == set(FAULTS)
    for case in cases.values():
        faulty = FAULTS[name](case, PACK)
        if faulty is None:
            continue
        gaps = [*run_rule_checks(faulty, PACK, settings.reference_time.date()).gaps, *deterministic_gaps(faulty, PACK)]
        routes = routes_for(gaps)
        assert gaps and all(r.kind != "other" for r in routes), [g for g in gaps if gap_kind(g) == "other"]
        assert OWNER[name] in {r.target for r in routes}, (name, [(r.kind, r.target) for r in routes])


def test_confirmed_challenges_are_routed_by_their_kind():
    model = "(raised by nvidia/nemotron, confirmed by code)"
    assert [r.target for r in routes_for([
        f"missing mandate: mandated task FM-HPC-01 is not in the plan {model}",
        f"part not in evidence: part TF-HPC-9999 is not in the evidence it cites {model}",
        f"unsupported number: claim [3] states 67, which none of EV-DIA-05 evidence contains {model}",
        f"deadline after bound: deadline 2026-12-01 is after 2026-10-27, the bound of 10 cycles {model}",
        f"wrong engine: the question names FD003-A100, the brief is about FD001-A031 {model}",
    ])] == ["knowledge", "data", "maint_planner", "maint_planner", None]
    assert named_parts(["part TF-HPC-9999 cites unknown evidence ['EV-MNT-03']"]) == ["TF-HPC-9999"]
    assert all(target is None or target in {"data", "knowledge", "prognostics", "diagnostics", "maint_planner"}
               for target, _ in ROUTES.values())


def _without_task(task_id):
    real = maint_planner.tasks_addressing
    return lambda *args, **kwargs: [t for t in real(*args, **kwargs) if t["fmea_id"] != task_id]


def test_missing_mandate_is_repaired_by_knowledge(settings, monkeypatch):
    """The World Model's addresses edge for FM-HPC-01 is gone, so the planner cannot find the task AD-2026-17-04
    mandates. Rule checks catch it; the planner routes it to Knowledge, which binds the task from the document's
    requires edge; the rebuilt plan carries it and passes."""
    monkeypatch.setattr(maint_planner, "tasks_addressing", _without_task("FM-HPC-01"))
    case = execute_run(HPC, settings).case
    assert case.status == "needs_review" and "FM-HPC-01" in case.recommendation.task_ids
    first = next(v for v in case.verdicts if v.outcome == "revise")
    assert [(r.kind, r.target) for r in first.routes] == [("missing_mandate", "knowledge")]
    handoffs = [(e.target_agent, e.output_summary) for e in _events(case, "planner", "route")]
    assert handoffs[0][0] == "knowledge" and handoffs[0][1].startswith("missing_mandate from rule checks round 1")
    binding = next(e for e in case.evidence.values() if e.content.get("binding"))
    assert binding.produced_by == "knowledge" and binding.content["task_ids"] == ["FM-HPC-01"]
    assert any(binding.id in c.evidence_ids for c in case.recommendation.rationale)
    log = [(e.actor, e.decision) for e in derive_log(case)]
    assert ("planner", "routed gap") in log and ("knowledge", "ran repair") in log


def test_without_knowledge_the_same_gap_stays_open(settings, monkeypatch):
    """Counterfactual: routed to the maintenance planner instead, nothing new arrives and the case closes."""
    monkeypatch.setattr(maint_planner, "tasks_addressing", _without_task("FM-HPC-01"))
    monkeypatch.setitem(routing.ROUTES, "missing_mandate", ("maint_planner", "rebuild the plan"))
    case = execute_run(HPC, settings).case
    assert case.status == "insufficient_evidence"
    assert len(_events(case, "maint_planner")) == 3 and not _events(case, "knowledge", "repair")


class _DropsPartRow(SnapshotSource):
    """Stock lookups that lose one part's row: on the first call only (a partial read) or always."""

    def __init__(self, always: bool = False):
        super().__init__()
        self.always, self.calls = always, 0

    def parts(self, part_numbers):
        self.calls += 1
        result = super().parts(part_numbers)
        if self.always or self.calls == 1:
            result.frame = result.frame[result.frame.part_number != "TF-ENG-9001"].reset_index(drop=True)
        return result


def test_lost_stock_row_is_requeried_by_the_data_agent(settings):
    case = execute_run(HPC, settings, data=_DropsPartRow()).case
    assert case.status == "needs_review"
    first = next(v for v in case.verdicts if v.outcome == "revise")
    assert [(r.kind, r.target) for r in first.routes] == [("part_not_in_evidence", "data")]
    repair = _events(case, "data", "repair")
    assert len(repair) == 1 and "stock rows for TF-ENG-9001" in repair[0].output_summary
    lease = next(p for p in case.recommendation.parts if p.part_number == "TF-ENG-9001")
    cited = case.evidence[lease.evidence_ids[0]]
    assert cited.produced_by == "data" and cited.content["requery"] and cited.content["requested"] == ["TF-ENG-9001"]


def test_a_requery_that_finds_nothing_leaves_the_gap_and_closes_at_the_round_limit(settings):
    case = execute_run(HPC, settings, data=_DropsPartRow(always=True)).case
    assert case.status == "insufficient_evidence"
    repairs = _events(case, "data", "repair")
    assert len(repairs) == 2 and all("the gap stays" in e.output_summary for e in repairs)
    assert len(_events(case, "maint_planner")) == 3 and case.round == 3
    assert any("TF-ENG-9001 is not in the catalogue" in g for g in case.verdicts[-1].gaps)


def test_missing_past_cases_are_searched_again_by_diagnostics(settings, monkeypatch):
    real, calls = SqliteStore.search_memory, []

    def flaky(self, query, top_n=5, tags=None, threshold=0.0):
        calls.append(top_n)
        return [] if top_n == 3 and calls.count(3) == 1 else real(self, query, top_n=top_n, tags=tags,
                                                                     threshold=threshold)

    monkeypatch.setattr(SqliteStore, "search_memory", flaky)
    case = execute_run(HPC, settings).case
    assert case.status == "needs_review" and len(case.findings.similar_cases) == 3
    critic = next(v for v in case.verdicts if v.gate == "critic" and v.outcome == "revise")
    assert [(r.kind, r.target) for r in critic.routes] == [("similar_cases_missing", "diagnostics")]
    assert "found 3 past cases" in _events(case, "diagnostics", "repair")[0].output_summary


def test_a_gap_no_agent_can_fix_closes_at_once(settings, monkeypatch):
    monkeypatch.setattr(assurance.rules, "asset_matches",
                        lambda case: ["intent asset FD001-A070 does not match resolved FD001-A031"])
    case = execute_run(HPC, settings).case
    assert case.status == "insufficient_evidence" and len(_events(case, "maint_planner")) == 1
    route = _events(case, "planner", "route_gaps")[0]
    assert route.target_agent == "close" and route.output_summary.startswith("no agent can fix wrong_engine")
    assert ("planner", "no agent can fix") in [(e.actor, e.decision) for e in derive_log(case)]


def test_the_longest_path_stays_inside_the_step_limit(settings, monkeypatch):
    """Follow-ups for a disagreement plus a critic gap that no repair closes: three rounds, then the round limit."""
    monkeypatch.setattr(SqliteStore, "search_memory",
                        lambda self, query, top_n=5, tags=None, threshold=None: [])
    case = execute_run(GOLDEN["FD001-A070"], settings).case
    assert case.status == "insufficient_evidence" and case.round == 3
    assert [v.outcome for v in case.verdicts if v.gate == "critic"] == ["revise", "revise", "reject"]
    assert len([e for e in case.trace if e.action == "follow_up"]) == 2
    assert len([e for e in case.trace if e.agent_name == "diagnostics" and e.action == "repair"]) == 2
