"""The inspection form in the brief: open when the verdict asks for an inspection, example data labelled as such, a
recorded finding stored append-only for the engine, and the brief re-run on it."""

import json

import pytest
from streamlit.testing.v1 import AppTest

from settings import ROOT

DISAGREE = "Methods disagree · FD001-A070"


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CONTEXT_SQLITE_PATH", str(tmp_path / "context.sqlite"))
    return AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=120)


def _open(app, label):
    app.run()
    next(b for b in app.button if b.label == label).click().run()


def _button(app, label):
    return next(b for b in app.button if b.label == label)


def _markdown(app) -> str:
    return " ".join(str(m.value) for m in app.markdown)


def _record(app):
    _button(app, "Record finding and re-run the brief").click().run()


def test_an_inspect_verdict_opens_the_form_and_an_example_finding_reruns_the_brief(app, tmp_path):
    _open(app, DISAGREE)
    assert not app.exception
    form = next(e for e in app.expander if e.label.startswith("Add inspection finding"))
    assert form.label.endswith("the brief asks for an inspection")
    assert _button(app, "Record finding and re-run the brief").disabled, "needs an inspector, what was seen, a result"
    _button(app, "Load example finding: beyond limits").click().run()
    assert any("Example data loaded" in str(i.value) for i in app.info)
    assert app.selectbox(key="ins_parameter_FD001-A070").value == "HPC-TIP"
    assert app.number_input(key="ins_value_FD001-A070").value == pytest.approx(0.82)
    _record(app)
    assert not app.exception
    stored = (tmp_path / "inspections" / "FD001-A070.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(stored) == 1 and json.loads(stored[0])["example"] is True
    assert any("Recorded inspection finding" in str(s.value) for s in app.success)
    rendered = _markdown(app)
    assert "Inspection findings" in rendered and "beyond limits" in rendered and "example data" in rendered
    assert "Remove the engine before further flight" in rendered
    assert "Inspection finding beyond limits" in rendered and "Potentially reportable" in rendered
    assert "EV-INS-01" in rendered


def test_a_finding_without_a_limit_needs_the_inspectors_result_and_is_marked_inspector_classified(app, tmp_path):
    _open(app, DISAGREE)
    key = "FD001-A070"
    app.selectbox(key=f"ins_kind_{key}").set_value("visual").run()
    app.selectbox(key=f"ins_module_{key}").set_value("combustor").run()
    app.text_area(key=f"ins_observation_{key}").input("Liner burn-through at the 4 o'clock position.").run()
    app.text_input(key=f"ins_inspector_{key}").input("B. Inspector").run()
    assert _button(app, "Record finding and re-run the brief").disabled, "no limit and no stated result"
    app.radio(key=f"ins_stated_{key}").set_value("at_limit").run()
    _record(app)
    assert not app.exception
    rendered = _markdown(app)
    assert "inspector-classified" in rendered and "at the limit" in rendered
    assert (tmp_path / "inspections" / f"{key}.jsonl").exists()
