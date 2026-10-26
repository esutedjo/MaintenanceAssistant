import json

import pytest
from streamlit.testing.v1 import AppTest

from casefile.decision_log import verify_log
from settings import ROOT


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CONTEXT_SQLITE_PATH", str(tmp_path / "context.sqlite"))
    return AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=120)


@pytest.fixture
def drafts(monkeypatch, tmp_path):
    import tools.writeback as writeback

    monkeypatch.setattr(writeback, "DRAFT_PATH", tmp_path / "draft_work_orders.jsonl")
    return tmp_path


def _run_fan_case(app):
    app.run()
    next(b for b in app.button if b.label == "Fan swap · FD003-A100").click().run()
    next(b for b in app.button if b.label == "Run").click().run()


def _button(app, label):
    return next(b for b in app.button if b.label == label)


def _acks(app):
    return [c for c in app.checkbox if c.key and c.key.startswith("ack_")]


def _reason(app):
    return next(t for t in app.text_input if t.key and t.key.startswith("urgent_reason_"))


def test_brief_offers_feedback_on_each_cited_past_case(app):
    _run_fan_case(app)
    assert not app.exception
    radios = [r for r in app.radio if r.key and r.key.startswith("fb_")]
    assert len(radios) == 3
    assert all(r.value == "Follow my decision" for r in radios)
    assert any(b.label == "Approve and draft work order" for b in app.button)


def test_approve_with_one_case_marked_wrong_reports_trust_changes(app, drafts):
    _run_fan_case(app)
    first = next(r for r in app.radio if r.key and r.key.startswith("fb_"))
    first.set_value("Wrong").run()
    app.text_input(key="signoff_name").input("A. Engineer").run()
    for ack in _acks(app):
        ack.check().run()
    _reason(app).input("Lease engine fitted tonight").run()
    _button(app, "Approve and draft work order").click().run()
    assert not app.exception
    notes = [c.value for c in app.caption if c.value and str(c.value).startswith("Case memory updated")]
    assert notes, "approve ran but the UI did not report case-memory feedback"
    note = notes[0]
    assert note.count("wrong") == 1 and note.count("helpful") == 2
    assert (drafts / "draft_work_orders.jsonl").exists()
    assert any("not a certificate of release to service" in str(s.value) for s in app.success)
    signed = json.loads(next((drafts / "decision_logs").glob("*.jsonl")).read_text(encoding="utf-8").splitlines()[-1])
    assert signed["acknowledged"][0]["reason"] == "Lease engine fitted tonight"
    rendered = " ".join(str(m.value) for m in app.markdown)
    assert "Approved despite the urgent escalation because: Lease engine fitted tonight" in rendered


def test_sign_off_needs_a_name_and_approve_needs_each_escalation_ticked(app, drafts):
    _run_fan_case(app)
    assert _button(app, "Approve and draft work order").disabled and _button(app, "Reject").disabled
    assert any(t.label.startswith("Decision log (") for t in app.tabs)
    app.text_input(key="signoff_name").input("A. Engineer").run()
    app.text_input(key="signoff_licence").input("TEST-0001").run()
    acks = _acks(app)
    assert len(acks) == 1 and "urgent escalation" in acks[0].label
    assert _button(app, "Approve and draft work order").disabled and not _button(app, "Reject").disabled
    acks[0].check().run()
    assert _button(app, "Approve and draft work order").disabled, "an urgent escalation also needs a reason"
    assert any("say why in the box above" in str(c.value) for c in app.caption)
    _reason(app).input("Lease engine fitted tonight").run()
    assert not _button(app, "Approve and draft work order").disabled
    _button(app, "Reject").click().run()
    assert not app.exception
    assert any("signed by A. Engineer" in str(i.value) for i in app.info)
    logs = list((drafts / "decision_logs").glob("*.jsonl"))
    assert len(logs) == 1 and verify_log(logs[0])[0]
    last = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[-1])
    assert (last["decision"], last["signer"]["licence_ref"]) == ("rejected", "TEST-0001")
    assert [a["rule"] for a in last["acknowledged"]] == ["rul_below_lead_time"]
    rendered = " ".join(str(m.value) for m in app.markdown)
    assert "dl-chip human" in rendered and "licence TEST-0001" in rendered


def _markdown(app) -> str:
    return " ".join(str(m.value) for m in app.markdown)


def test_help_button_opens_the_panel_from_anywhere_and_explains_the_open_brief(app):
    _run_fan_case(app)
    rendered = _markdown(app)
    assert '<abbr title="RUL (remaining useful life)' in rendered, "abbreviations in the brief carry a tooltip"
    assert any("not for operational use" in str(c.value) for c in app.caption)
    app.button(key="help_open").click().run()
    assert not app.exception
    labels = [t.label for t in app.tabs]
    assert labels.count("This screen") == 2 and labels.count("Ask Cleris") == 2, "dialog plus the Help tab"
    rendered = _markdown(app)
    assert "Not certified or approved by any aviation authority" in rendered
    assert "Advisory decision support." in rendered and "keep, inspect or remove an engine" in rendered
    assert "The coloured box says what to do with FD003-A100" in rendered and "<dt>Decision log</dt>" in rendered
    app.text_input(key="dlg_glossary_search").input("ESN").run()
    assert app.session_state["help_topic"] == "overview", "the dialog stays open across a rerun"
    assert "engine serial number" in _markdown(app)
    app.text_input(key="dlg_ask_question").input("What is a borescope?").run()
    app.button(key="dlg_ask_go").click().run()
    assert not app.exception
    assert any("Sample mode: AI help is off" in str(i.value) for i in app.info)
    assert "A thin camera pushed inside the engine" in _markdown(app)


def test_section_link_opens_help_at_that_section(app):
    _run_fan_case(app)
    app.button(key="help_signoff").click().run()
    assert not app.exception and app.session_state["help_topic"] == "signoff"
    rendered = _markdown(app)
    assert "**Your decision.** You stay in charge." in rendered
    assert "Certificate of release to service" in rendered and "Hash chain" in rendered
