import pytest

from agents.critic import deterministic_gaps
from assurance.rules import run_rule_checks
from data.db import load_pack
from evaluation.faults import FAULTS
from evaluation.run_eval import has_quantity
from graph.runner import execute_run
from settings import Settings

PACK = load_pack()


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    settings = Settings(_env_file=None, sample_mode=True,
                        context_sqlite_path=tmp_path_factory.mktemp("ctx") / "context.sqlite")
    questions = {"FD001-A031": "What should I do about FD001-A031?",
                 "FD003-A100": "How long can FD003-A100 keep flying and what is wrong with it?",
                 "FD001-A070": "The two remaining-life estimates for FD001-A070 look different. What should I do?"}
    return settings, {a: execute_run(q, settings).case for a, q in questions.items()}


def _gaps(case, settings):
    return [*run_rule_checks(case, PACK, settings.reference_time.date()).gaps, *deterministic_gaps(case, PACK)]


def test_clean_golden_briefs_pass_the_gates(golden):
    settings, cases = golden
    assert all(_gaps(c, settings) == [] for c in cases.values())


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_every_seeded_fault_is_caught_where_it_applies(golden, name):
    settings, cases = golden
    applied = [f for f in (FAULTS[name](c, PACK) for c in cases.values()) if f is not None]
    assert applied, f"{name} applies to none of the golden cases"
    assert all(_gaps(f, settings) for f in applied)


def test_quantity_detector_ignores_identifiers():
    assert not has_quantity("Limits for FM-HPC-01 follow SB-TF-72-0142 3.B and AMM-TF-R3 72-31-00.")
    assert has_quantity("RUL lower bound 7 cycles; part TF-HPC-1100 has 21 d lead time.")
