import pytest
from streamlit.testing.v1 import AppTest

from settings import ROOT


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CONTEXT_SQLITE_PATH", str(tmp_path / "context.sqlite"))
    return AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=120)


def test_brief_offers_feedback_on_each_cited_past_case(app):
    app.run()
    next(b for b in app.button if b.label == "Fan swap · FD003-A100").click().run()
    next(b for b in app.button if b.label == "Run").click().run()
    assert not app.exception
    radios = [r for r in app.radio if r.key and r.key.startswith("fb_")]
    assert len(radios) == 3
    assert all(r.value == "Follow my decision" for r in radios)
    assert any(b.label == "Approve and draft work order" for b in app.button)


def test_approve_with_one_case_marked_wrong_reports_trust_changes(app, monkeypatch, tmp_path):
    import tools.writeback as writeback

    monkeypatch.setattr(writeback, "DRAFT_PATH", tmp_path / "draft_work_orders.jsonl")
    app.run()
    next(b for b in app.button if b.label == "Fan swap · FD003-A100").click().run()
    next(b for b in app.button if b.label == "Run").click().run()
    first = next(r for r in app.radio if r.key and r.key.startswith("fb_"))
    first.set_value("Wrong").run()
    next(b for b in app.button if b.label == "Approve and draft work order").click().run()
    assert not app.exception
    notes = [c.value for c in app.caption if c.value and str(c.value).startswith("Case memory updated")]
    assert notes, "approve ran but the UI did not report case-memory feedback"
    note = notes[0]
    assert note.count("wrong") == 1 and note.count("helpful") == 2
    assert (tmp_path / "draft_work_orders.jsonl").exists()
