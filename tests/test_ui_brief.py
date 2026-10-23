import pandas as pd

from ui.brief import driving_sensor_figure, fault_label, fmt_rul, sensor_label, verdict_headline, verdict_tone
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


def test_fleet_lists_active_snapshot_engines():
    assets = list_active_assets()
    assert "FD001-A001" in set(assets.asset_id)
    assert "FD003-A100" in set(assets.asset_id)
    assert (assets.asset_id.str.startswith("FD") ).all()
