"""The critic's free-text notes are screened before the engineer sees them: a note code shows false (an evidence ID
"not listed" that is in the case file, an ID the case file does not have) or one repeating a challenge code refuted is
dropped, kept in the trace for audit; near-duplicates are folded."""

import json

import pytest

from agents.critic import critic_prompt
from assurance.challenges import all_claims
from assurance.notes import fold, screen
from casefile.decision_log import derive_log
from data.db import load_pack
from graph.runner import execute_run
from settings import Settings
from tools import llm_recordings

PACK = load_pack()
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
HPC = "What should I do about FD001-A031?"


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    return Settings(_env_file=None, sample_mode=True,
                    context_sqlite_path=tmp_path_factory.mktemp("ctx") / "context.sqlite")


@pytest.fixture(scope="module")
def plain(settings):
    return execute_run(HPC, settings).case


def test_a_note_calling_an_existing_record_missing_is_dropped(plain):
    false = "Claim 0 cites EV-PRG-01 for RUL A, but EV-PRG-01 is not listed in the evidence; only EV-PRG-02 is."
    unknown = "The slot claim should cite EV-DAT-99."
    fine = "Consider stating the AD compliance window in the action line."
    kept, dropped = screen(plain, [false, unknown, fine, "missing EV-MNT-03 for the parts"], [])
    assert kept == [fine]
    assert dropped[0] == f"says EV-PRG-01 is missing, but EV-PRG-01 is in the case file: {false}"
    assert dropped[1] == f"cites EV-DAT-99, which the case file does not have: {unknown}"
    assert dropped[2].startswith("says EV-MNT-03 is missing")
    kept, _ = screen(plain, ["EV-PRG-02 is available; EV-PRG-01 gives the range 5-11."], [])
    assert len(kept) == 1, "naming a record is not calling it missing"


def test_a_note_repeating_a_refuted_challenge_is_dropped(plain):
    deadline = "Claim 9 derives deadline 2026-10-22 from a bound of 0 cycles, which equals today."
    range_note = "Claim 0 states the 10th-90th range 5-11 without support."
    other_claim = "Claim 10 states 5 parts."
    refuted = [{"kind": "deadline_after_bound"}, {"kind": "unsupported_number", "claim": 0, "value": 5}]
    kept, dropped = screen(plain, [deadline, range_note, other_claim], refuted)
    assert kept == [other_claim]
    assert dropped == [f"repeats the deadline after bound challenge code refuted: {deadline}",
                       f"repeats the unsupported number challenge code refuted: {range_note}"]


def test_near_duplicates_fold_into_one_line():
    refuted = ["unsupported number challenge refuted by code: claim [0] does not state 5",
               "unsupported number challenge refuted by code: claim [0] does not state 11",
               "deadline after bound challenge refuted by code: deadline 2026-10-22 is within the bound (2026-10-22)"]
    lines, folded = fold(refuted, marker="challenge refuted by code")
    assert folded == 1 and lines == [
        "unsupported number challenge refuted by code (2 challenges): claim [0] does not state 5 or 11", refuted[2]]
    notes, folded = fold(["The slot date is not cited in the plan card.", "The slot date is not cited in plan card.",
                          "The slot date is not cited in the plan card."])
    assert folded == 2 and notes == ["The slot date is not cited in the plan card. (and 1 similar note)"]


def test_the_brief_shows_only_screened_notes_and_the_trace_keeps_the_raw_ones(plain, settings, tmp_path,
                                                                             monkeypatch):
    claims = all_claims(plain)
    swap = next(i for i, c in enumerate(claims) if c.text.startswith("RUL lower bound 0.0 days is shorter"))
    deadline = next(i for i, c in enumerate(claims) if c.text.startswith("Deadline 2026-10-22"))
    notes = [
        "Claim 0 cites EV-PRG-01 for RUL A 10th-90th percentile 5-11, but EV-PRG-01 is not listed in the evidence; "
        "only EV-PRG-02 is available, so the ML model percentile range cannot be verified.",
        f"Claim {deadline} derives deadline 2026-10-22 from RUL lower bound 0 cycles, so the deadline equals the "
        "current date.",
        f"Claim {swap} states all parts are on hand but the next free slot is 1 day away; EV-MNT-03 shows long lead "
        "times.",
        "Consider stating the AD compliance window in the action line.",
    ]
    reply = {"outcome": "accept", "gaps": notes, "challenges": [
        {"kind": "unsupported_number", "claim": 0, "value": 5, "detail": "5"},
        {"kind": "unsupported_number", "claim": 0, "value": 11, "detail": "11"},
        {"kind": "deadline_after_bound", "claim": None, "value": None, "detail": "today"},
        {"kind": "unsupported_number", "claim": swap, "value": 1, "detail": "1 d"}]}
    path = tmp_path / "recordings.json"
    llm_recordings.save([llm_recordings.Recording(
        purpose="critic", case="test", prompt_sha256=llm_recordings.prompt_hash(critic_prompt(plain, PACK)),
        model=MODEL, captured_at="2026-10-26T10:00:00Z", text=json.dumps(reply))], path)
    monkeypatch.setattr(llm_recordings, "RECORDINGS", path)
    case = execute_run(HPC, settings).case
    verdict = case.verdicts[-1]
    assert verdict.outcome == "accept" and verdict.recorded
    assert [d.split(": ", 1)[0] for d in verdict.dropped_notes] == [
        "says EV-PRG-01 is missing, but EV-PRG-01 is in the case file",
        "repeats the deadline after bound challenge code refuted",
        "repeats the unsupported number challenge code refuted"]
    assert verdict.gaps[0] == notes[3] and len(verdict.gaps) == 4         # 1 note + 3 refuted lines (2 folded)
    assert "unsupported number challenge refuted by code (2 challenges): claim [0] does not state 5 or 11" in \
        verdict.gaps
    audit = next(e for e in case.trace if e.action == "screen_notes")
    assert audit.agent_name == "critic" and "4 model note(s): 1 shown, 3 dropped" in audit.output_summary
    assert f"Raw notes: (1) {notes[0]}" in audit.output_summary and "recorded Nemotron response" in \
        audit.output_summary
    review = next(e for e in derive_log(case) if e.actor == MODEL)
    assert "Raised 4 checkable challenges and 1 note" in review.rationale
    assert "code dropped 3 notes it showed false or repeating a refuted challenge" in review.rationale
    assert [e.decision for e in derive_log(case) if e.actor == "critic"] == [
        "challenge refuted", "challenge refuted", "challenge refuted", "accepted"]
