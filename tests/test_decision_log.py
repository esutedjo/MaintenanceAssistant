"""The decision log: derived from the case file in order, sign-off bound to the brief it signs, append-only, and a
hash chain that shows an edited, deleted or cut entry."""

import json
from types import SimpleNamespace

import pytest

from agents.critic import checked_challenges
from assurance.challenges import all_claims
from casefile.decision_log import (
    Signer, _digest, append_entries, brief_ref, derive_log, log_path, signoff_entry, verify_log,
)
from casefile.schemas import Verdict
from data.db import load_pack
from evaluation.faults import FAULTS
from graph.runner import execute_run
from settings import Settings
from tests.test_intake_llm import _settings as live_settings
from tools.llm import LlmClient, LlmResponse
from tools.model_tools import ModelTools

PACK = load_pack()
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
FAN = "How long can FD003-A100 keep flying and what is wrong with it?"
HPC = "What should I do about FD001-A031?"


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    return Settings(_env_file=None, sample_mode=True,
                    context_sqlite_path=tmp_path_factory.mktemp("ctx") / "context.sqlite")


@pytest.fixture(scope="module")
def fan(settings):
    return execute_run(FAN, settings).case


@pytest.fixture(scope="module")
def hpc(settings):
    return execute_run(HPC, settings).case


def _steps(entries):
    return [(e.actor_type, e.actor, e.decision, e.round) for e in entries]


def test_revise_loop_and_escalation_are_logged_in_order(fan):
    entries = derive_log(fan)
    assert _steps(entries) == [
        ("agent", "intake", "resolved engine", None),
        ("agent", "planner", "no follow-up needed", None),
        ("agent", "maint_planner", "proposed plan", 1),
        ("gate", "rule_checks", "accepted", 1),
        ("gate", "critic", "sent back for revision", 1),
        ("agent", "planner", "routed gap", 2),
        ("agent", "maint_planner", "revised plan", 2),
        ("gate", "rule_checks", "accepted", 2),
        ("gate", "critic", "accepted", 2),
        ("gate", "escalation", "escalated", 2),
    ]
    assert "distinguish" in entries[4].rationale and "rul_below_lead_time" in entries[-1].rationale
    assert entries[5].rationale.startswith("To maint_planner: driver_signature from critic round 1")
    assert [e.timestamp for e in entries] == sorted(e.timestamp for e in entries)
    cited = {i for e in entries for i in e.evidence_ids}
    assert cited and cited <= set(fan.evidence)
    assert {e.run_id for e in entries} == {fan.case_id}


def test_mandate_and_each_escalation_get_an_entry(hpc):
    entries = derive_log(hpc)
    doc = next(d for d in hpc.findings.documents if d.mandatory)
    mandate = next(e for e in entries if e.decision == "applied mandate")
    assert mandate.actor == "knowledge" and doc.doc_id in mandate.rationale
    assert mandate.evidence_ids == [doc.evidence_id]
    assert len([e for e in entries if e.decision == "escalated"]) == len(hpc.escalations) == 3


def test_refuted_challenge_is_logged_as_a_model_review_and_a_code_check(tmp_path, monkeypatch):
    def chat(self, messages, **kwargs):
        text = ('{"outcome": "revise", "gaps": ["slot not cited"], '
                '"challenges": [{"kind": "missing_mandate", "claim": null, "value": null, "detail": "AD task?"}]}')
        return LlmResponse(text=text, provider="meterless", requested_model="meterless/auto", model=MODEL,
                           duration_s=0.1)

    monkeypatch.setattr(LlmClient, "chat", chat)
    case = execute_run(HPC, live_settings(tmp_path)).case
    critic = [e for e in derive_log(case) if e.actor in {MODEL, "critic"}]
    assert [(e.actor_type, e.decision, e.round) for e in critic] == [
        ("model", "reviewed the brief", 1), ("gate", "challenge refuted", 1), ("gate", "accepted", 1)]
    assert "1 checkable challenge and 1 note" in critic[0].rationale
    assert critic[1].rationale.startswith("Missing mandate challenge refuted by code")


def test_confirmed_challenge_sends_the_brief_back_and_model_notes_do_not(hpc, settings):
    """Uses the critic's own wording, so the log's markers cannot drift from agents/critic.py."""
    faulty = FAULTS["claim_number_altered"](hpc, PACK)
    cohort = next(i for i, c in enumerate(all_claims(hpc)) if c.text.startswith("Similar past removals"))
    confirmed, refuted = checked_challenges(faulty, SimpleNamespace(settings=settings, pack=PACK), [
        {"kind": "unsupported_number", "claim": cohort, "value": 67}, {"kind": "missing_mandate"}], MODEL)
    event = next(e for e in hpc.trace if e.agent_name == "critic")
    verdict = Verdict(round=1, gate="critic", outcome="revise", gaps=[*confirmed, "a free-text note", *refuted],
                      model=MODEL)
    entries = derive_log(hpc.model_copy(update={"trace": [event], "verdicts": [verdict]}))
    assert [(e.actor_type, e.decision) for e in entries] == [
        ("model", "reviewed the brief"), ("gate", "challenge confirmed"), ("gate", "challenge refuted"),
        ("gate", "sent back for revision")]
    assert entries[-1].rationale.startswith("1 gap to fix") and "free-text note" not in entries[-1].rationale


def test_asked_back_and_failed_runs_are_logged(settings, monkeypatch):
    asked = execute_run("Is the engine ok?", settings).case
    assert _steps(derive_log(asked)) == [("agent", "intake", "asked back", None)]

    def broken(*args, **kwargs):
        raise RuntimeError("model file corrupt")

    monkeypatch.setattr(ModelTools, "drivers", broken)
    failed = execute_run("How long can FD003-A100 keep flying?", settings).case
    assert {("prognostics", "failed"), ("brief", "failed")} <= {(e.actor, e.decision) for e in derive_log(failed)}


def test_signoff_is_bound_to_the_brief_it_signs(hpc):
    entry = signoff_entry(hpc, "approved", Signer(name=" A. Engineer ", licence_ref=""), "go",
                          {"EV-DIA-02": "helpful"}, "WO-DRAFT-x")
    assert (entry.actor_type, entry.actor, entry.signer.licence_ref) == ("human", "A. Engineer", None)
    assert entry.timestamp.utcoffset().total_seconds() == 0
    assert entry.brief == brief_ref(hpc) and entry.brief.run_id == hpc.case_id
    assert "not a certificate of release to service" in entry.rationale
    changed = hpc.recommendation.model_copy(update={"action": "Continue in service"})
    assert brief_ref(hpc.model_copy(update={"recommendation": changed})).recommendation_sha256 != \
        entry.brief.recommendation_sha256
    with pytest.raises(ValueError):
        Signer(name="  ")


def test_approve_reason_is_stored_on_urgent_acknowledgements_only(hpc, tmp_path):
    assert [e.severity for e in hpc.escalations] == ["urgent", "urgent", "info"]      # info: sb_adoption
    approved = signoff_entry(hpc, "approved", Signer(name="A. Engineer"), acknowledged=hpc.escalations,
                             reason=" AD borescope booked tonight ")
    assert [a.reason for a in approved.acknowledged] == ["AD borescope booked tonight"] * 2 + [None]
    rejected = signoff_entry(hpc, "rejected", Signer(name="A. Engineer"), acknowledged=hpc.escalations,
                             reason="not stored on reject")
    assert [a.reason for a in rejected.acknowledged] == [None, None, None]
    warning = hpc.escalations[0].model_copy(update={"severity": "warning"})
    assert signoff_entry(hpc, "approved", Signer(name="A. Engineer"), acknowledged=[warning],
                         reason="r").acknowledged[0].reason is None
    path = log_path(tmp_path, hpc.case_id)
    rows = append_entries(path, [*derive_log(hpc), approved])
    assert rows[-1]["acknowledged"][0]["reason"] == "AD borescope booked tonight"
    assert verify_log(path, rows[-1]["hash"])[0]
    assert all("reason" not in row and row["acknowledged"] == [] for row in rows[:-1]), \
        "agent, gate and model entries carry no new field"


def _write(tmp_path, case):
    path = log_path(tmp_path, case.case_id)
    return path, append_entries(path, [*derive_log(case), signoff_entry(case, "approved", Signer(name="A. Engineer"))])


def _rewrite(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_hash_chain_catches_an_edited_deleted_or_cut_entry(tmp_path, fan):
    path, rows = _write(tmp_path, fan)
    head, lines = rows[-1]["hash"], path.read_text(encoding="utf-8").splitlines()
    assert verify_log(path, head) == (True, f"hash chain intact, {len(rows)} entries")

    assert json.loads(lines[4])["decision"] == "sent back for revision"
    edited = {**json.loads(lines[4]), "decision": "accepted"}
    _rewrite(path, [*lines[:4], json.dumps(edited), *lines[5:]])
    assert verify_log(path, head) == (False, "entry 5 does not match its hash: it was edited")
    edited["hash"] = _digest({k: v for k, v in edited.items() if k != "hash"})
    _rewrite(path, [*lines[:4], json.dumps(edited), *lines[5:]])
    assert verify_log(path, head) == (False, "entry 6 does not chain to the entry before it")

    _rewrite(path, [*lines[:4], *lines[5:]])
    assert verify_log(path, head) == (False, "entry 5 has sequence 6: an entry was deleted or reordered")

    _rewrite(path, lines[:-1])
    ok, note = verify_log(path)
    assert not ok and note.startswith("unverifiable: the chain of"), "never 'intact' without a head hash"
    assert verify_log(path, head) == (False, "the last entry is not the one the work order references: "
                                             "entries were removed")
    path.unlink()
    assert verify_log(path, head)[1] == "unverifiable: the log has no entries (missing or empty file)"


def test_log_is_append_only_and_a_correction_is_a_new_entry(tmp_path, fan):
    path, rows = _write(tmp_path, fan)
    before = path.read_bytes()
    assert append_entries(path, derive_log(fan)) == rows and path.read_bytes() == before
    correction = signoff_entry(fan, "rejected", Signer(name="A. Engineer"), "signed the wrong engine")
    after = append_entries(path, [*derive_log(fan), correction])
    assert path.read_bytes().startswith(before) and len(after) == len(rows) + 1
    assert after[-1]["prev_hash"] == rows[-1]["hash"] and verify_log(path, after[-1]["hash"])[0]
