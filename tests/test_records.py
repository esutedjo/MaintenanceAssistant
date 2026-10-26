"""Records hardening at sign-off: info escalations are notes the log records as seen, the brief as shown is stored and
its SHA-256 is in the engineer's entry, every stored entry carries the versions the case ran on, the log's head hash
is anchored outside the log, and a potentially reportable occurrence gets a note (Cleris files nothing)."""

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from assurance.escalation import mandate_missed, potentially_reportable
from casefile.decision_log import Signer, read_log, run_id_of, verify_log
from data.db import load_pack
from graph.runner import execute_run
from settings import ROOT, Settings
from tools.versions import MODEL_FILES
from tools.writeback import approve_case, reject_case, signoff_anchors

PACK = load_pack()
ON = date(2026, 10, 22)
SIGNER = Signer(name="A. Engineer", role="B1 engineer")
HPC = "What should I do about FD001-A031?"


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    return Settings(_env_file=None, sample_mode=True,
                    context_sqlite_path=tmp_path_factory.mktemp("records") / "context.sqlite")


@pytest.fixture(scope="module")
def hpc(settings):
    return execute_run(HPC, settings).case


def _path(stored: str) -> Path:
    path = Path(stored)
    return path if path.is_absolute() else ROOT / path


def test_info_escalations_need_no_tick_and_the_entry_records_them_as_noted(hpc, settings, tmp_path):
    assert [(e.rule, e.severity) for e in hpc.escalations] == [
        ("document_mandate", "urgent"), ("rul_below_lead_time", "urgent"), ("sb_adoption", "info")]
    record = approve_case(hpc, settings, path=tmp_path / "wo.jsonl", signer=SIGNER, acknowledged=[0, 1],
                          reason="AD borescope booked tonight")
    human = record["decision_log"]["entries"][-1]
    assert [a["rule"] for a in human["acknowledged"]] == ["document_mandate", "rul_below_lead_time"]
    assert [(n["rule"], n["severity"], n["reason"]) for n in human["noted"]] == [("sb_adoption", "info", None)]
    with pytest.raises(ValueError, match="not acknowledged: rul_below_lead_time$"):
        approve_case(hpc, settings, path=tmp_path / "again.jsonl", signer=SIGNER, acknowledged=[0], reason="r")


def test_the_brief_as_shown_is_stored_and_its_hash_is_in_the_engineers_entry(hpc, settings, tmp_path):
    record = reject_case(hpc, settings, path=tmp_path / "wo.jsonl", signer=SIGNER, comment="borescope first")
    snapshot = record["decision_log"]["entries"][-1]["brief_snapshot"]
    data = _path(snapshot["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == snapshot["sha256"] == record["brief_snapshot"]["sha256"]
    assert _path(snapshot["path"]).name == f"{hpc.case_id}-{snapshot['sha256'][:12]}.json"
    shown = json.loads(data)
    assert shown == hpc.model_dump(mode="json") and shown["findings"]["documents"], "documents used are in it"


def test_every_stored_entry_carries_the_versions_the_case_ran_on(hpc, settings, tmp_path):
    record = reject_case(hpc, settings, path=tmp_path / "wo.jsonl", signer=SIGNER)
    stamp = record["versions"]
    assert all(row["versions"] == stamp for row in record["decision_log"]["entries"])
    assert stamp["asset_pack"] == f"turbofan_cmapss@{PACK['version']}" and stamp["llm"] == "none"
    assert set(stamp["models"]) == set(MODEL_FILES) and all(re.fullmatch(r"[0-9a-f]{64}", h)
                                                            for h in stamp["models"].values())
    assert stamp["code"] == "unknown" or re.fullmatch(r"[0-9a-f]{40}(\+dirty)?", stamp["code"])


def test_the_head_hash_is_anchored_outside_the_log_and_a_replaced_log_fails_against_it(tmp_path):
    settings = Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")
    case = execute_run(HPC, settings).case
    record = reject_case(case, settings, path=tmp_path / "wo.jsonl", signer=SIGNER)
    head = record["decision_log"]["head_hash"]
    anchors = signoff_anchors(case.case_id, run_id_of(case), settings)
    assert anchors == {"trace": head, "case memory": head}
    log = tmp_path / "decision_logs" / f"{case.case_id}.jsonl"
    ok, note = verify_log(log, anchors=anchors)
    assert ok and "trace anchor matches" in note and "case memory anchor matches" in note
    shorter = [json.dumps(row) for row in read_log(log)[:-1]]            # the sign-off entry cut off
    log.write_text("\n".join(shorter) + "\n", encoding="utf-8")
    ok, note = verify_log(log)
    assert not ok and note.startswith("unverifiable"), "the chain alone is consistent, but cannot prove it is whole"
    ok, note = verify_log(log, anchors=anchors)
    assert not ok and note.startswith(f"the trace anchor references head {head[:12]}, which is not in the log")


def test_a_mandate_the_plan_would_complete_late_is_potentially_reportable(hpc):
    assert potentially_reportable(hpc, PACK, ON) is None                # the AD is due 2026-10-27, slot 2026-10-23
    late = hpc.model_copy(deep=True)
    late.recommendation.slot = ON + timedelta(days=9)
    [missed] = mandate_missed(late, PACK, ON)
    assert missed.startswith("AD-2026-17-04 (g) due 2026-10-27") and "first hangar slot 2026-10-31" in missed
    note = potentially_reportable(late, PACK, ON)
    assert note.severity == "info" and note.message.startswith("Potentially reportable: check your occurrence")
    assert "The decision to report stays with people." in note.message
