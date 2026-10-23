from agents.critic import _parse, deterministic_gaps, drivers_vs_fault
from casefile.schemas import AssetRef, CaseFile, Claim, Driver, FaultModeFinding
from data.db import load_pack

PACK = load_pack()


def test_driver_gap_fires_when_shap_ignores_discriminating_sensors():
    case = CaseFile(
        case_id="c", question="q",
        asset=AssetRef(asset_id="FD003-A100", asset_type="turbofan_cmapss", pack_version="0.1.0", variant="FD003"),
        findings={"fault_modes": [FaultModeFinding(failure_mode="fan_degradation", probability=1.0, evidence_id="EV-DIA-01")],
                  "drivers": [Driver(sensor="s_9", component="hpc", contribution=-14.7, evidence_id="EV-PRG-03")]},
        evidence={},
    )
    gap = drivers_vs_fault(case, PACK)
    assert gap and "phi" in gap.lower() or "s_12" in (gap or "")
    case.findings.claims = [Claim(text="Sensors that distinguish Fan degradation are phi, BPR, W31, W32, P30.",
                                  evidence_ids=["EV-DIA-01"], confidence="medium")]
    assert drivers_vs_fault(case, PACK) is None
    assert deterministic_gaps(case, PACK) == []


def test_parse_accepts_fenced_json_and_string_gaps():
    assert _parse('```json\n{"outcome": "ACCEPT", "gaps": "missing stock"}\n```') == {
        "outcome": "accept", "gaps": ["missing stock"]}
    assert _parse('{"outcome": "revise", "gaps": ["a", "b"]}')["gaps"] == ["a", "b"]
