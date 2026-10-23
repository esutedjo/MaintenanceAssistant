from data.db import load_pack
from tools.data_access import SnapshotSource
from ui.methods import drivers_figure, health_index_figure, method_facts


def test_facts_come_from_model_artifacts():
    facts = method_facts(load_pack())
    assert facts["train_engines"] == 160
    assert facts["a"]["window"] == 30
    assert len(facts["a"]["sensors"]) == 14 and len(facts["b"]["sensors"]) == 9
    assert facts["b"]["thresholds"]["p10"] < facts["b"]["thresholds"]["p50"] < facts["b"]["thresholds"]["p90"]
    assert 0 < facts["a"]["mae"] < facts["b"]["mae"]


def test_health_index_chart_draws_trend_for_worn_engine():
    cycles = SnapshotSource().cycles("FD003-A100").frame
    fig, note = health_index_figure(cycles, load_pack())
    assert note is None
    assert any(t.name == "fitted trend" for t in fig.data)


def test_health_index_chart_explains_missing_trend_for_healthy_engine():
    cycles = SnapshotSource().cycles("FD001-A001").frame
    fig, note = health_index_figure(cycles, load_pack())
    assert fig is not None and "healthy band" in note
    assert not any(t.name == "fitted trend" for t in fig.data)


def test_drivers_chart_has_one_bar_per_sensor():
    drivers = [{"sensor": "s_9", "component": "hpc", "contribution": -14.7},
               {"sensor": "s_13", "component": "fan", "contribution": -4.8}]
    fig = drivers_figure(drivers, load_pack())
    assert list(fig.data[0].x) == [-14.7, -4.8]
