import pandas as pd

from data.build_fmea import PARTS_CSV, build, signature, validate
from data.db import PACK_DIR, load_pack


def _fmea() -> pd.DataFrame:
    return pd.read_csv(PACK_DIR / "fmea.csv", dtype={"qty": "Int64"})


def test_pack_fmea_is_valid_and_covers_every_failure_mode():
    fmea = build()
    assert set(fmea.failure_mode) == set(load_pack()["failure_modes"])
    assert fmea.sensor_signature.str.len().gt(0).all()


def test_discriminating_signature_separates_hpc_from_fan():
    pack = load_pack()
    assert "s_12 phi down" in signature(pack, "hpc_degradation", discriminating_only=True)
    assert "s_12 phi up" in signature(pack, "fan_degradation", discriminating_only=True)
    assert "s_3 T30" not in signature(pack, "hpc_degradation", discriminating_only=True)


def test_validate_rejects_unknown_part_and_wrong_mode_part():
    fmea = _fmea()
    fmea.loc[0, "part_number"] = "TF-XXX-0000"
    fmea.loc[1, "part_number"], fmea.loc[1, "qty"] = "TF-FAN-2101", 1
    errors = validate(fmea, load_pack(), pd.read_csv(PARTS_CSV))
    assert any("TF-XXX-0000 not in parts catalogue" in e for e in errors)
    assert any("TF-FAN-2101 belongs to fan_degradation" in e for e in errors)


def test_validate_rejects_component_mismatch_and_bad_severity():
    fmea = _fmea()
    fmea.loc[0, "component"] = "fan"
    fmea.loc[0, "trigger_severity"] = "critical"
    errors = validate(fmea, load_pack(), pd.read_csv(PARTS_CSV))
    assert any("does not match pack" in e for e in errors)
    assert any("trigger_severity critical" in e for e in errors)
