from data.db import load_pack
from ui.help import GLOSSARY, SECTION_HELP, plain_summary


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
    assert all(term and meaning for term, meaning in GLOSSARY)
