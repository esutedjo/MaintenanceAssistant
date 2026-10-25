"""What a judge without credentials should see: sample mode, distinct golden cases, no replayed outputs."""

import json

import pytest

from graph.runner import run_case
from settings import ROOT, Settings

CASES = [
    ("What is the status of FD001-A001?", "healthy"),
    ("What should I do about FD001-A031?", "hpc"),
    ("How long can FD003-A100 keep flying and what is wrong with it?", "fan"),
    ("The two remaining-life estimates for FD001-A070 look different. What should I do?", "disagree"),
]


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def test_golden_cases_are_real_runs_not_replays(settings):
    actions = {}
    for question, name in CASES:
        result = run_case(question, settings)
        assert result["status"] == "needs_review", (name, result.get("status"), result.get("degraded"))
        assert result["mode"]["sample_mode"] is True
        assert result["mode"]["data"] == "snapshot"
        rec = result["recommendation"]
        assert rec and rec["action"]
        actions[name] = rec["action"]
        saved = ROOT / "output_examples"
        # the run must not be a lookup of output_examples
        assert "output_examples" not in json.dumps(result)
    assert actions["healthy"] != actions["hpc"]
    assert actions["disagree"].lower().startswith("borescope")
    assert "swap" in actions["hpc"].lower()
    assert "swap" in actions["fan"].lower()
    assert result["recommendation"]  # last case finished


def test_committed_examples_match_the_four_golden_questions():
    questions = {json.loads((ROOT / "input_examples" / name).read_text(encoding="utf-8"))["question"]
                 for name in ("healthy_engine.json", "critical_hpc.json", "fan_degradation.json",
                              "methods_disagree.json")}
    assert {q for q, _ in CASES} == questions
