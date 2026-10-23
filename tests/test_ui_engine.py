from pathlib import Path

from data.db import load_pack
from ui.engine import component_probabilities, credit, engine_figure, photos


def test_fault_modes_map_to_pack_components():
    findings = {"fault_modes": [{"failure_mode": "fan_degradation", "probability": 0.9},
                                {"failure_mode": "hpc_degradation", "probability": 0.1}]}
    assert component_probabilities(findings, load_pack()) == {"fan": 0.9, "hpc": 0.1}


def test_diagram_draws_every_module_mirrored_and_highlights_fault():
    pack = load_pack()
    fig = engine_figure(pack, {"hpc": 1.0}, drivers=["s_9"])
    modules = [t for t in fig.data if t.fill == "toself"]
    assert len(modules) == 2 * len(pack["diagram"])
    hpc = [t for t in modules if t.text.startswith("HPC")]
    assert all(t.fillcolor.startswith("rgba(225,29,72") for t in hpc)
    pins = fig.data[-1]
    assert "Nc" in pins.text and "T2" not in pins.text


def test_every_listed_photo_exists_with_licence_and_source():
    pack = load_pack()
    listed = pack["images"]["items"]
    found = photos(pack)
    assert len(found) == len(listed)
    for item in found:
        assert Path(item["path"]).is_file()
        assert item["license"] and item["source"].startswith("https://commons.wikimedia.org/")
        assert item["author"] in credit(item)
    assert {p["component"] for p in photos(pack, "fan")} == {"fan"}
