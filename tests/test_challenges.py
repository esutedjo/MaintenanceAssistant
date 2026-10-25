"""Nemotron's critic challenges change the outcome only when code confirms them against the evidence."""

from types import SimpleNamespace

import pytest

from agents.critic import checked_challenges
from assurance.challenges import all_claims, unsupported_numbers, verify
from assurance.rules import run_rule_checks
from data.db import load_pack
from evaluation.faults import FAULTS
from graph.runner import execute_run, run_case
from settings import Settings
from tools.llm import LlmClient, LlmResponse

PACK = load_pack()
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    return Settings(_env_file=None, sample_mode=True,
                    context_sqlite_path=tmp_path_factory.mktemp("ctx") / "context.sqlite")


@pytest.fixture(scope="module")
def hpc(settings):
    return execute_run("What should I do about FD001-A031?", settings).case


def _cohort_index(case) -> int:
    return next(i for i, c in enumerate(all_claims(case)) if c.text.startswith("Similar past removals"))


def test_every_number_in_a_clean_brief_is_supported(hpc):
    assert all(not unsupported_numbers(hpc, c, PACK) for c in all_claims(hpc))
    assert run_rule_checks(hpc, PACK, None).outcome == "accept"


def test_an_altered_number_is_caught_by_the_rule_checks(hpc, settings):
    faulty = FAULTS["claim_number_altered"](hpc, PACK)
    gaps = run_rule_checks(faulty, PACK, settings.reference_time.date()).gaps
    assert any("which its cited evidence" in g and "67" in g for g in gaps)


def test_unsupported_number_challenge_is_confirmed_only_when_the_evidence_lacks_it(hpc, settings):
    ref, i = settings.reference_time.date(), _cohort_index(hpc)
    assert verify(hpc, PACK, ref, {"kind": "unsupported_number", "claim": i, "value": 27}) == \
        (False, f"{', '.join(all_claims(hpc)[i].evidence_ids)} supports 27")
    assert not verify(hpc, PACK, ref, {"kind": "unsupported_number", "claim": i, "value": 99})[0]
    faulty = FAULTS["claim_number_altered"](hpc, PACK)
    ok, reason = verify(faulty, PACK, ref, {"kind": "unsupported_number", "claim": i, "value": 67})
    assert ok and "states 67" in reason


def test_structural_challenges_follow_the_evidence(hpc, settings):
    ref = settings.reference_time.date()
    assert not verify(hpc, PACK, ref, {"kind": "missing_mandate"})[0]
    assert verify(FAULTS["mandate_dropped"](hpc, PACK), PACK, ref, {"kind": "missing_mandate"})[0]
    assert verify(FAULTS["fabricated_part"](hpc, PACK), PACK, ref, {"kind": "part_not_in_evidence"})[0]
    assert verify(FAULTS["deadline_past_rul"](hpc, PACK), PACK, ref, {"kind": "deadline_after_bound"})[0]
    assert verify(FAULTS["wrong_asset"](hpc, PACK), PACK, ref, {"kind": "wrong_engine"})[0]
    assert not verify(hpc, PACK, ref, {"kind": "vibes"})[0]


def test_critic_turns_confirmed_challenges_into_gaps_and_refuted_ones_into_notes(hpc, settings):
    ctx = SimpleNamespace(settings=settings, pack=PACK)
    faulty = FAULTS["claim_number_altered"](hpc, PACK)
    i = _cohort_index(hpc)
    confirmed, refuted = checked_challenges(faulty, ctx, [
        {"kind": "unsupported_number", "claim": i, "value": 67},
        {"kind": "missing_mandate"},
    ], MODEL)
    assert len(confirmed) == 1 and "confirmed by code" in confirmed[0] and MODEL in confirmed[0]
    assert len(refuted) == 1 and refuted[0].startswith("missing mandate challenge refuted by code")


def test_live_critic_with_a_refuted_challenge_still_accepts(tmp_path, monkeypatch):
    from tests.test_intake_llm import _settings

    def chat(self, messages, **kwargs):
        text = ('{"outcome": "revise", "gaps": ["slot not cited"], '
                '"challenges": [{"kind": "missing_mandate", "claim": null, "value": null, "detail": "AD task?"}]}')
        return LlmResponse(text=text, provider="meterless", requested_model="meterless/auto", model=MODEL,
                           duration_s=0.1)

    monkeypatch.setattr(LlmClient, "chat", chat)
    result = run_case("What should I do about FD001-A031?", _settings(tmp_path))
    last = result["verdicts"][-1]
    assert result["status"] == "needs_review" and last["outcome"] == "accept"
    assert any(g.startswith("missing mandate challenge refuted by code") for g in last["gaps"])
