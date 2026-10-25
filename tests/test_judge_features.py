"""Stress test, decision boundaries and fleet triage: the features a skeptical judge uses to check the brief."""

import pytest

from data.db import load_pack
from graph.runner import execute_run
from settings import Settings
from tools.triage import decision_kind, triage_row
from ui.brief import decision_boundaries
from ui.stress import gate_result, stress_rows

PACK = load_pack()


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    return Settings(_env_file=None, sample_mode=True,
                    context_sqlite_path=tmp_path_factory.mktemp("ctx") / "context.sqlite")


@pytest.fixture(scope="module")
def hpc(settings):
    return execute_run("What should I do about FD001-A031?", settings).case


def test_stress_test_catches_every_applicable_mistake_and_passes_the_clean_brief(hpc, settings):
    reference = settings.reference_time.date()
    assert gate_result(hpc, PACK, reference)["caught_by"] is None
    rows = stress_rows(hpc, PACK, reference)
    applied = [r for r in rows if r["applies"]]
    assert len(rows) == 16 and len(applied) >= 10
    assert all(r["caught_by"] in {"rule checks", "critic"} and r["gaps"] for r in applied)


def test_decision_boundaries_explain_the_swap_and_the_mandate(hpc):
    lines = decision_boundaries(hpc.model_dump(mode="json"), PACK)
    text = " ".join(lines)
    assert "Severity severe" in text and "under 25" in text
    assert "Lease-engine swap" in text and "1 d to the first free slot" in text
    assert "AD-2026-17-04" in text and "No change in the numbers removes it" in text


def test_decision_boundaries_explain_a_disagreement(settings):
    case = execute_run("The two remaining-life estimates for FD001-A070 look different. What should I do?",
                       settings).case
    text = " ".join(decision_boundaries(case.model_dump(mode="json"), PACK))
    assert "Inspection, not a repair" in text and "limit 20" in text


def test_triage_row_classifies_the_decision(hpc, settings):
    row = triage_row(hpc, settings.reference_time.date())
    assert row["asset_id"] == "FD001-A031" and row["decision"] == "remove, swap or repair"
    assert row["days_to_deadline"] == 0 and "rul below lead time" in row["escalations"]
    assert decision_kind("Continue in service; next scheduled inspection") == "keep flying"
    assert decision_kind("Borescope inspection; do not average the two remaining-life estimates") == "inspect"
    assert decision_kind(None) == "no recommendation"
