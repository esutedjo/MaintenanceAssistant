"""Engineering orders: a signed fleet decision on a recommended service bulletin, stored append-only and hash-chained,
cited by every later brief for that fleet instead of the sb_adoption note. No decision: today's behaviour."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from assurance.rules import run_rule_checks
from casefile.decision_log import Signer, derive_log
from data.db import load_pack
from graph.runner import execute_run
from settings import ROOT, Settings
from tools.engineering_orders import (
    describe,
    fleet_decisions,
    read_orders,
    record_order,
    store_path,
    verify_orders,
)

PACK = load_pack()
DISAGREE = "The two remaining-life estimates for FD001-A070 look different. What should I do?"
FAN = "How long can FD003-A100 keep flying and what is wrong with it?"


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, sample_mode=True, context_sqlite_path=tmp_path / "context.sqlite")


def _order(settings, decision="decline", fleet="FD001", name="Jane Doe", reference="EO-2026-114"):
    return record_order(settings, fleet=fleet, doc_id="SB-TF-72-0142", decision=decision,
                        signer=Signer(name=name, role="Powerplant engineering"), pack=PACK, reference=reference)


def test_an_order_is_signed_stamped_and_hash_chained(settings):
    first = _order(settings)
    second = _order(settings, decision="adopt", name="A. Lead")
    assert store_path(settings).name == "engineering_orders.jsonl"
    assert store_path(settings).parent == settings.context_sqlite_path.parent      # beside the case memory
    assert (first["eo_id"], first["seq"], second["eo_id"], second["seq"]) == ("EO-FD001-001", 1, "EO-FD001-002", 2)
    assert first["decided_at"].endswith("Z") and first["signer"]["name"] == "Jane Doe"
    assert first["versions"]["asset_pack"] == f"{PACK['asset_type']}@{PACK['version']}" and first["versions"]["code"]
    assert second["prev_hash"] == first["hash"]
    assert verify_orders(read_orders(settings)) == (True, "hash chain intact, 2 engineering orders")
    decisions, note = fleet_decisions(settings, "FD001")
    assert note is None and decisions["SB-TF-72-0142"]["eo_id"] == "EO-FD001-002", "the later order supersedes"
    assert fleet_decisions(settings, "FD003") == ({}, None), "a decision covers its own fleet only"
    assert describe(first).startswith("SB-TF-72-0142 declined for fleet FD001 by Jane Doe on ")


@pytest.mark.parametrize("kwargs, message", [
    ({"name": ""}, "signer's name"), ({"fleet": "FD009"}, "unknown fleet"), ({"decision": "maybe"}, "adopt or decline"),
])
def test_an_order_needs_a_name_a_known_fleet_and_a_decision(settings, kwargs, message):
    args = {"decision": "decline", "fleet": "FD001", "name": "Jane Doe", **kwargs}
    with pytest.raises(ValueError, match=message):
        record_order(settings, fleet=args["fleet"], doc_id="SB-TF-72-0142", decision=args["decision"],
                     signer=args["name"], pack=PACK)
    assert not store_path(settings).exists()


def test_an_edited_file_is_not_trusted(settings):
    _order(settings)
    path = store_path(settings)
    row = json.loads(path.read_text(encoding="utf-8"))
    row["decision"] = "adopt"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    decisions, note = fleet_decisions(settings, "FD001")
    assert decisions == {} and "fails its hash chain" in note
    case = execute_run(DISAGREE, settings).case
    assert "sb_adoption" in [e.rule for e in case.escalations], "an untrusted order decides nothing"
    assert any("fleet decisions ignored" in d for d in case.degraded)
    with pytest.raises(ValueError, match="hash chain"):
        _order(settings)


def test_no_decision_keeps_todays_note(settings):
    case = execute_run(DISAGREE, settings).case
    assert [e.rule for e in case.escalations] == ["rul_divergence", "sb_adoption"]
    sb = next(d for d in case.findings.documents if d.doc_id == "SB-TF-72-0142")
    assert (sb.group, sb.fleet_decision) == ("recommended", None)


def test_declined_bulletin_is_cited_and_the_plan_follows_the_manual(settings):
    order = _order(settings)
    case = execute_run(DISAGREE, settings).case
    assert case.status == "needs_review" and [e.rule for e in case.escalations] == ["rul_divergence"]
    said = f"SB-TF-72-0142 declined for fleet FD001 by Jane Doe on {order['decided_at'][:10]} (EO-FD001-001)"
    claims = [c.text for c in [*case.findings.claims, *case.recommendation.rationale]]
    assert any(said in c and "so the plan follows AMM-TF-R3 72-31-00" in c for c in claims)
    assert f"Limits and intervals for FM-HPC-01 follow AMM-TF-R3 72-31-00 (manual): {said}." in claims
    sb = next(d for d in case.findings.documents if d.doc_id == "SB-TF-72-0142")
    assert (sb.group, sb.fleet_decision) == ("reference", "decline")
    assert case.evidence[sb.decision_evidence_id].source == "engineering_orders:EO-FD001-001"
    assert case.recommendation.work_package.sections[0].governed_by == "AMM-TF-R3 72-31-00"
    assert run_rule_checks(case, PACK, settings.reference_time.date()).gaps == []
    entry = next(e for e in derive_log(case) if e.decision == "recorded fleet decision")
    assert entry.actor == "Jane Doe" and "Declined SB-TF-72-0142 for fleet FD001 (EO-FD001-001)" in entry.rationale
    fan = execute_run(FAN, settings).case                       # another fleet: untouched
    assert not any(d.fleet_decision for d in fan.findings.documents)


def test_adopted_bulletin_is_cited_and_the_plan_follows_it(settings):
    _order(settings, decision="adopt")
    case = execute_run(DISAGREE, settings).case
    assert "sb_adoption" not in [e.rule for e in case.escalations]
    rationale = [c.text for c in case.recommendation.rationale]
    assert any(t.startswith("Limits and intervals for FM-HPC-01 follow SB-TF-72-0142 3.B (service bulletin): "
                            "SB-TF-72-0142 adopted for fleet FD001 by Jane Doe") for t in rationale)
    assert any(c.text.startswith("Recommended, adopted by engineering order: SB-TF-72-0142 3.B")
               for c in case.findings.claims)
    assert case.recommendation.work_package.sections[0].governed_by == "SB-TF-72-0142 3.B"
    assert run_rule_checks(case, PACK, settings.reference_time.date()).gaps == []


def test_the_ui_records_a_fleet_decision_on_the_note_and_reruns(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CONTEXT_SQLITE_PATH", str(tmp_path / "context.sqlite"))
    app = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=120)
    app.run()
    next(b for b in app.button if b.label == "Methods disagree · FD001-A070").click().run()
    assert not app.exception
    assert any(e.label.startswith(r"Record fleet decision on SB\-TF\-72\-0142 for FD001") for e in app.expander)
    assert any(e.label.startswith("Work package · 1 part · 4.5 manhours · 1 RII") for e in app.expander)
    record = next(b for b in app.button if b.label == "Record fleet decision")
    assert record.disabled, "a fleet decision needs the signer's name"
    app.text_input(key="eo_FD001_SB-TF-72-0142_name").input("Jane Doe").run()
    next(b for b in app.button if b.label == "Record fleet decision").click().run()
    assert not app.exception
    rows = [json.loads(line) for line in (tmp_path / "engineering_orders.jsonl").read_text(encoding="utf-8")
            .splitlines()]
    assert [(r["eo_id"], r["decision"], r["signer"]["name"]) for r in rows] == [("EO-FD001-001", "decline", "Jane Doe")]
    assert any(r"Recorded engineering order SB\-TF\-72\-0142 declined for fleet FD001" in str(s.value)
               for s in app.success)
    assert not any(e.label.startswith("Record fleet decision") for e in app.expander), "the note no longer fires"
