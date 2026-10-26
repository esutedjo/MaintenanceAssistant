import pandas as pd

from ui.brief import (
    approval_gate, check_summary, decision_log_rows, driving_sensor_figure, escalation_ack_label, escalation_view,
    fault_label, fmt_rul, sensor_label, trace_rows, verdict_headline, verdict_tone, world_eligibility,
)
from ui.fleet import list_active_assets
from data.db import load_pack


def test_verdict_tone_marks_urgent_escalation():
    case = {
        "status": "needs_review",
        "recommendation": {"action": "Swap", "confidence": "medium", "deadline": "2026-10-24"},
        "escalations": [{"severity": "urgent", "message": "RUL below lead time"}],
    }
    assert verdict_tone(case) == "urgent"
    assert verdict_headline(case) == "Swap"


def test_fmt_rul_shows_range_and_cap():
    assert fmt_rul({"rul": 26.7, "lower": 21.7, "upper": 125, "at_cap": False}, with_range=True) == "27  (22–125)"
    assert "cap" in fmt_rul({"rul": 125, "lower": 125, "upper": 125, "at_cap": True})


def test_sensor_and_fault_labels_come_from_pack():
    pack = load_pack()
    assert "phi" in sensor_label("s_12", pack)
    assert fault_label("hpc_degradation", pack) == "HPC degradation"


def test_chart_plots_driving_sensors():
    pack = load_pack()
    cycles = pd.DataFrame({"cycle": [1, 2, 3], "s_9": [1.0, 1.1, 1.2], "s_12": [2.0, 2.1, 2.2]})
    fig = driving_sensor_figure(cycles, [{"sensor": "s_9"}, {"sensor": "s_12"}], pack)
    assert fig is not None
    assert len(fig.data) == 2


def test_escalation_view_titles_and_cleans_actions():
    view = escalation_view({"rule": "rul_below_lead_time", "severity": "urgent", "message": "RUL bound 0 d.",
                            "actions": ["Propose expedite", "Propose engine swap"]})
    assert view["title"].startswith("The engine wears out")
    assert view["proposed"] == ["expedite", "engine swap"]
    mandate = escalation_view({"rule": "document_mandate", "severity": "urgent", "message": "AD requires: Borescope",
                               "actions": ["Borescope", "Mandatory: cannot be removed or deferred by the agents"]})
    assert mandate["proposed"] == [] and mandate["notes"] == ["Cannot be removed or deferred by the agents"]


def test_world_eligibility_compares_graph_tasks_with_the_plan():
    case = {"recommendation": {"task_ids": ["FM-HPC-06", "FM-HPC-01"]},
            "evidence": {"EV-MNT-02": {"produced_by": "maint_planner", "source": "sqlite:world/hpc_degradation",
                                       "content": {"task_ids": ["FM-HPC-01", "FM-HPC-06", "FM-HPC-06"]}}}}
    graph = world_eligibility(case)
    assert graph["listed"] == ["FM-HPC-01", "FM-HPC-06"] and graph["used"] == ["FM-HPC-06", "FM-HPC-01"]
    assert graph["off_graph"] == [] and graph["evidence_id"] == "EV-MNT-02"
    assert world_eligibility({"evidence": {}}) is None


def test_check_summary_counts_revisions_and_names_the_model():
    case = {"verdicts": [{"gate": "rule_checks", "outcome": "accept"}, {"gate": "critic", "outcome": "revise"},
                         {"gate": "rule_checks", "outcome": "accept"},
                         {"gate": "critic", "outcome": "accept", "model": "nvidia/nemotron-3-ultra-550b-a55b"}],
            "escalations": [{}], "evidence": {"EV-DAT-01": {}}}
    chips = dict(check_summary(case))
    assert "critic accepted after 1 revision" in chips
    assert "LLM critic: nvidia/nemotron-3-ultra-550b-a55b" in chips
    assert chips["1 escalation"] == "flag" and "1 evidence records" in chips


def test_trace_rows_mark_revisions_and_targets():
    rows = trace_rows([
        {"agent_name": "planner", "action": "plan_tasks", "status": "success", "target_agent": "data,prognostics"},
        {"agent_name": "critic", "action": "disprove", "status": "needs_revision", "output_summary": "revise"},
    ])
    assert rows[0]["to"] == "data, prognostics" and rows[0]["tone"] == "ok"
    assert rows[1]["tone"] == "flag" and rows[1]["to"] == "maintenance planner"


def test_decision_log_rows_label_actors_and_bind_the_signoff():
    rows = decision_log_rows([
        {"actor_type": "gate", "actor": "rule_checks", "decision": "sent back for revision", "round": 1,
         "timestamp": "2026-10-25T23:15:03.110475Z", "rationale": "1 check failed.", "evidence_ids": ["EV-MNT-02"]},
        {"actor_type": "model", "actor": "nvidia/nemotron-3-ultra-550b-a55b", "decision": "reviewed the brief",
         "timestamp": "2026-10-25T23:15:04Z"},
        {"actor_type": "human", "actor": "A. Engineer", "decision": "approved", "timestamp": "2026-10-25T23:20:00Z",
         "signer": {"name": "A. Engineer", "role": "B1 engineer", "licence_ref": "TEST-0001"}, "comment": "go",
         "case_feedback": {"EV-DIA-02": "not_helpful"}, "brief": {"case_id": "c1", "recommendation_sha256": "ab" * 32},
         "wo_id": "WO-DRAFT-1", "hash": "f" * 64,
         "acknowledged": [{"rule": "document_mandate", "severity": "urgent", "message": "AD requires FM-HPC-01.",
                           "reason": "Borescope booked for tonight"}]},
    ])
    gate, model, human = rows
    assert (gate["actor"], gate["chip"], gate["tone"], gate["time"]) == ("rule checks", "gate", "flag", "23:15:03 UTC")
    assert gate["evidence_ids"] == ["EV-MNT-02"] and gate["round"] == 1
    assert model["actor"].startswith("nvidia/") and model["chip"] == "model" and model["details"] == []
    assert (human["chip"], human["tone"], human["hash"]) == ("engineer", "ok", "f" * 12)
    assert human["details"][0] == "Signed 2026-10-25 23:20:00 UTC · B1 engineer · licence TEST-0001"
    assert human["details"][1] == "Brief c1 · recommendation fingerprint abababababab"
    assert "Past-case ratings: EV-DIA-02 not helpful" in human["details"]
    assert "Escalations acknowledged: urgent document mandate" in human["details"]
    assert "Approved despite the urgent escalation because: Borescope booked for tonight" in human["details"]


def test_approve_waits_for_the_signer_and_every_escalation_tick():
    escalations = [{"rule": "document_mandate", "severity": "urgent", "message": "AD-2026-17-04 requires FM-HPC-01."},
                   {"rule": "rul_below_lead_time", "severity": "urgent", "message": "RUL bound 0 d."}]
    assert approval_gate("", [], []) == (False, "To approve or reject, type your name in Signed by.")
    assert approval_gate("A. Engineer", [], []) == (True, "")
    assert approval_gate(" ", escalations, [True, False]) == (
        False, "To approve or reject, type your name in Signed by; to approve, also tick 1 more escalation as read.")
    assert approval_gate("A. Engineer", escalations, [False, False]) == (
        False, "To approve, tick 2 more escalations as read. Reject needs no acknowledgement.")
    assert approval_gate("A. Engineer", escalations, [True, True]) == (
        False, "To approve despite the urgent escalation, say why in the box above. Reject needs no reason.")
    assert approval_gate("A. Engineer", escalations, [True, True], "  ")[0] is False
    assert approval_gate("A. Engineer", escalations, [True, True], "Lease engine fitted tonight") == (True, "")
    warning = [{"rule": "rul_divergence", "severity": "warning", "message": "A and B differ."}]
    assert approval_gate("A. Engineer", warning, [True]) == (True, ""), "only an urgent escalation needs a reason"
    assert escalation_ack_label(escalations[0]) == ("I have read the urgent escalation: Mandatory document action. "
                                                    "AD-2026-17-04 requires FM-HPC-01.")


def test_fleet_lists_active_snapshot_engines():
    assets = list_active_assets()
    assert "FD001-A001" in set(assets.asset_id)
    assert "FD003-A100" in set(assets.asset_id)
    assert (assets.asset_id.str.startswith("FD") ).all()
