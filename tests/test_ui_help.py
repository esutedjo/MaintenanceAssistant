import json
import re

import pytest
from PIL import Image

from data.db import load_pack
from settings import ROOT
from ui.brief import ESCALATION_TITLES, check_summary, escalation_view, verdict_headline, verdict_meta
from ui.help import (
    CLEARANCE, DISCLAIMER, DISCLAIMER_SHORT, FLOW_STEPS, HOW_TO_READ, INTENDED_USE, KEYFRAMES,
    SECTION_HELP, TOPICS, flow_html, glossary, glossary_html, guide_html, plain_summary, screen_guide,
    search_glossary, sensor_entries, terms_in, tooltip, topic_entries,
)

PACK = load_pack()
GOLDEN = ["critical_hpc", "fan_degradation", "healthy_engine", "methods_disagree"]
# A standalone abbreviation (HPC, RUL, FD001), or the letter prefix of an ID that continues with digits (ESN-700131).
ABBREVIATION = re.compile(r"(?<![\w-])([A-Z]{2,}\d*)(?=$|[^\w-]|-\d)|(?<![\w-])([A-Z]{2,}\d+)(?=-)")


def _golden(name: str) -> dict:
    return json.loads((ROOT / "output_examples" / f"{name}.json").read_text(encoding="utf-8"))


def _names(entries):
    return {n.lower() for e in entries for n in [e["term"], e["full"], *e["aliases"], *e["abbr"]] if n}


def _case(**overrides):
    case = {
        "status": "needs_review",
        "asset": {"asset_id": "FD003-A100"},
        "findings": {
            "rul_ml": {"rul": 26.7, "lower": 21.7, "upper": 125, "at_cap": False},
            "rul_trend": {"rul": 9.6, "lower": 2.9, "upper": 14.8, "at_cap": False},
            "fault_modes": [{"failure_mode": "fan_degradation", "probability": 1.0},
                            {"failure_mode": "hpc_degradation", "probability": 0.0}],
            "similar_cases": ["EV-DIA-02", "EV-DIA-03", "EV-DIA-04"],
        },
        "recommendation": {
            "action": "Swap in lease engine; module shop visit on the removed engine", "deadline": "2026-10-23",
            "parts": [{"part_number": "TF-FAN-2101", "qty": 1, "available": 0, "lead_time_days": 75}],
        },
        "verdicts": [{"gate": "critic", "outcome": "revise"}, {"gate": "critic", "outcome": "accept"}],
        "escalations": [],
    }
    case.update(overrides)
    return case


def test_summary_words_the_case_numbers():
    text = " ".join(plain_summary(_case(), load_pack()))
    assert "fan and bypass duct" in text
    assert "10–27 more flights" in text
    assert "TF-FAN-2101" in text and "75 days" in text
    assert "2026-10-23" in text
    assert "sending it back 1 time" in text
    assert "3 similar past engines" in text


def test_healthy_engine_says_no_wear():
    findings = {"rul_ml": {"rul": 120.9, "lower": 116.7, "upper": 125, "at_cap": False},
                "rul_trend": {"rul": 125, "lower": 125, "upper": 125, "at_cap": True},
                "fault_modes": [{"failure_mode": "hpc_degradation", "probability": 1.0}]}
    text = " ".join(plain_summary(_case(findings=findings, recommendation={"action": "Continue in service"}),
                                  load_pack()))
    assert "no clear sign of wear" in text
    assert "wearing out" not in text


def test_closed_case_explains_why():
    assert "stopped rather than guess" in plain_summary(_case(status="insufficient_evidence"), load_pack())[0]


def test_every_section_and_term_has_text():
    assert all(SECTION_HELP.values())
    entries = glossary(PACK)
    assert all(e["term"] and e["meaning"] and e["where"] for e in entries)
    assert len({e["term"].lower() for e in entries}) == len(entries), "duplicate glossary term"
    long = [e["term"] for e in entries if len(re.findall(r"(?<!e\.g)[.!?](?:\s|$)", e["meaning"])) > 2]
    assert long == [], "a meaning is one or two sentences"


def test_glossary_covers_the_terms_a_non_expert_meets():
    names = _names(glossary(PACK))
    for term in ["rul", "cycle", "flight", "esn", "hpc", "hpt", "lpc", "lpt", "fan", "egt", "c-mapss", "fd001",
                 "fd003", "ad", "sb", "amm", "fmea", "failure mode", "cmms", "mro", "camo", "part-145",
                 "certificate of release to service", "aog", "llp", "borescope", "shop visit", "lease engine swap",
                 "work order (draft)", "escalation", "evidence id", "percentile", "range", "planning bound",
                 "confidence", "h-mem", "world model", "zetaris", "nightshift", "nemotron", "decision log",
                 "hash chain", "sample mode", "live mode", "clearance"]:
        assert term in names, term
    for sensor in ["T30", "P30", "Ps30", "phi", "W31", "BPR", "T50"]:
        assert sensor.lower() in names, sensor


def test_every_abbreviation_in_the_brief_header_has_an_entry():
    """Verdict, check chips, context strip, escalation cards, plan tasks and key-number labels of the golden briefs."""
    names = _names(glossary(PACK))
    shown = [DISCLAIMER_SHORT, "Telemetry is NASA C-MAPSS (simulated)", *ESCALATION_TITLES.values(),
             "Flights left · method A (range)", "Flights left · method B (trend)", "Fault mode", "Similar past cases"]
    for name in GOLDEN:
        case = _golden(name)
        shown += [verdict_headline(case), verdict_meta(case), *(text for text, _ in check_summary(case))]
        for esc in case["escalations"]:
            view = escalation_view(esc)
            shown += [view["title"], view["message"], *view["notes"], *view["proposed"]]
        # The context strip: engine ID, serial number (the snapshot's ESN-7xxxxx form), fleet and status.
        shown += [case["asset"]["asset_id"], "ESN-700131", case["asset"]["variant"], "active"]
        shown += case["recommendation"]["task_ids"]
    found = {a or b for text in shown for a, b in ABBREVIATION.findall(text)}
    assert {"RUL", "HPC", "AD", "ESN", "FD001", "FD003"} <= found
    assert sorted(t for t in found if t.lower() not in names) == []


def test_sensor_entries_come_from_the_pack_and_say_which_tell_the_faults_apart():
    entries = {e["term"]: e for e in sensor_entries(PACK)}
    assert set(entries) == {spec["name"] for spec in PACK["sensors"].values()}
    assert "tells the faults apart" in entries["P30"]["meaning"] and "HPC outlet" in entries["P30"]["meaning"]
    assert "whichever part is wearing" in entries["T30"]["meaning"]
    assert "does not change with wear" in entries["T2"]["meaning"]
    assert entries["phi"]["aliases"] == ["s_12"]


@pytest.mark.parametrize("query, first", [
    ("RUL", "RUL"), ("hpc", "HPC"), ("Why are there two remaining-life numbers?", "RUL"),
    ("what is a borescope", "Borescope"), ("certificate of release to service", "Certificate of release to service"),
    ("What does p = 1.00 mean?", "p"), ("s_12", "phi"),
])
def test_search_finds_the_term_the_question_names(query, first):
    assert search_glossary(query, glossary(PACK))[0]["term"] == first


def test_search_is_empty_for_nonsense_and_whole_for_no_query():
    entries = glossary(PACK)
    assert search_glossary("xyzzy", entries) == []
    assert search_glossary("  ", entries) == entries
    assert [e["term"] for e in terms_in("Is the HPC or the fan worn?", entries)] == ["Fan", "HPC"]


def test_tooltips_explain_abbreviations_and_evidence_ids_and_escape_html():
    out = tooltip("RUL bound 0 d; AD-2026-17-04 requires FM-HPC-01 (EV-KNW-02) <b>", glossary(PACK))
    assert out.count("<abbr") == 3 and "&lt;b&gt;" in out
    assert '<abbr title="RUL (remaining useful life): ' in out
    assert ">EV-KNW-02</abbr>" in out and "produced by the knowledge (documents)" in out
    assert "FM-HPC-01" in out and ">HPC</abbr>" not in out, "an abbreviation inside an ID stays plain"
    assert tooltip(None, glossary(PACK)) == ""


def test_topic_terms_exist_and_render():
    entries = glossary(PACK)
    for topic, (title, note, terms) in TOPICS.items():
        assert note in SECTION_HELP and len(topic_entries(topic, entries)) == len(terms), topic
    html = glossary_html(topic_entries("signoff", entries))
    assert "Certificate of release to service" in html and "Where:" in html


@pytest.mark.parametrize("name", GOLDEN)
def test_screen_guide_words_the_open_brief(name):
    case = _golden(name)
    rows = dict(screen_guide(case, PACK))
    assert list(rows) == ["Verdict", "Deadline and confidence", "Two remaining-life numbers", "Fault mode", "Plan",
                          "Escalations", "Evidence tags", "Checks", "Decision log", "Sign-off"]
    rec, a, b = case["recommendation"], case["findings"]["rul_ml"], case["findings"]["rul_trend"]
    assert rec["action"] in rows["Verdict"] and rec["deadline"] in rows["Deadline and confidence"]
    assert f"**{a['rul']:.0f} flights**" in rows["Two remaining-life numbers"]
    assert f"between {a['lower']:.0f} and {a['upper']:.0f}" in rows["Two remaining-life numbers"]
    assert "not a certificate of release to service" in rows["Sign-off"]
    assert "hash chain" in rows["Decision log"]
    urgent = any(e["severity"] == "urgent" for e in case["escalations"])
    assert ("because an escalation here is urgent" in rows["Sign-off"]) == urgent
    assert ("more than 20" in rows["Two remaining-life numbers"]) == (name == "methods_disagree")


def test_screen_guide_without_a_brief_is_generic():
    rows = screen_guide(None, PACK)
    assert len(rows) == 10 and all(text for _, text in rows)
    assert "Red" in rows[0][1] and "Green" in rows[0][1]
    assert "<dt>Verdict</dt>" in guide_html(rows) and "<b>Red</b>" in guide_html(rows)


def test_help_text_says_what_leaves_the_machine_and_what_approval_is():
    assert "Nothing is sent anywhere until you approve" not in SECTION_HELP["decision"]
    assert "sample mode nothing leaves this machine" in SECTION_HELP["decision"].lower()
    assert "Nemotron critic through Nightshift before you approve" in SECTION_HELP["decision"]
    for text in (SECTION_HELP["signoff"], HOW_TO_READ[-1]):
        assert "not a certificate of release to service" in text and "name" in text and "urgent" in text
    assert "hash chain" in SECTION_HELP["signoff"] and "agent, gate, model and engineer" in SECTION_HELP["signoff"]
    assert "Not certified or approved by any aviation authority" in DISCLAIMER
    assert "never a release to service" in INTENDED_USE and "keep, inspect or remove" in CLEARANCE


def test_step_strip_has_seven_steps_and_its_photos_are_credited_pack_photos():
    assert len(FLOW_STEPS) == 7 and flow_html().count('class="flow-step"') == 7
    assert "Zetaris" in FLOW_STEPS[2][1] and "Nightshift" in FLOW_STEPS[5][1]
    credited = {item["file"]: item for item in PACK["images"]["items"]}
    for file, caption in KEYFRAMES:
        path = ROOT / PACK["images"]["directory"] / file
        assert path.is_file() and caption and credited[file]["author"] and credited[file]["license"]
        with Image.open(path) as image:
            assert image.width <= 1280
