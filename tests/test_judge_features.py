"""Stress test, decision boundaries and fleet triage: the features a skeptical judge uses to check the brief."""

import json
from collections import Counter

import pytest

from data.db import load_pack
from evaluation.faults import FAULTS
from graph.runner import execute_run
from settings import ROOT, Settings
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
    assert len(rows) == 19 and len(applied) >= 13          # 17 faults plus the two AD compliance-line faults
    assert all(r["caught_by"] in {"rule checks", "critic"} and r["gaps"] for r in applied)


def test_gates_catch_every_brief_nemotron_alone_was_given(settings):
    """README, "Gates against Nemotron alone": the golden-brief and fault pairs in evaluation/llm_critic_report.json,
    rebuilt the way uv run task eval-llm builds them, all stopped by the gates; the clean briefs pass."""
    rows = json.loads((ROOT / "evaluation" / "llm_critic_report.json").read_text(encoding="utf-8"))["rows"]
    reference = settings.reference_time.date()
    caught = Counter()
    for asset in sorted({r["asset"] for r in rows}):
        case = execute_run(f"What is wrong with {asset}, how long do I have, and what should I do?", settings).case
        assert gate_result(case, PACK, reference)["caught_by"] is None
        for row in rows:
            if row["asset"] == asset and row["fault"] != "clean":
                caught[gate_result(FAULTS[row["fault"]](case, PACK), PACK, reference)["caught_by"]] += 1
    assert caught == {"rule checks": 38, "critic": 4}


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
    assert decision_kind("No action proposed before the next scheduled inspection") == "keep flying"
    assert decision_kind("Continue in service; next scheduled inspection") == "keep flying"     # older briefs
    assert decision_kind("Borescope inspection; do not average the two remaining-life estimates") == "inspect"
    assert decision_kind(None) == "no recommendation"


def test_decision_boundaries_word_the_healthy_plan_as_the_brief_does(settings):
    case = execute_run("What is the status of FD001-A001?", settings).case
    text = " ".join(decision_boundaries(case.model_dump(mode="json"), PACK))
    assert "**No action proposed before the next scheduled inspection** because" in text
    assert "Kept in service" not in text
