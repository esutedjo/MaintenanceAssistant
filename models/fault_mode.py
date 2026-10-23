"""Fault-mode signature matcher. Each failure mode in the asset pack moves the sensors in a known direction; the
sensors whose direction differs between the modes a variant can show tell them apart. An engine's shift on those
sensors (recent level minus its own start, in fleet standard deviations) is scored against each mode's signature
and turned into probabilities with a softmax whose temperature is calibrated on run-to-failure engines."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from models.features import normalise
from models.rul import BASELINE_CYCLES, relative_to_own_start

RECENT_CYCLES = 10
SIGN = {"up": 1, "down": -1, "none": 0}


@dataclass
class FaultModeMatch:
    asset_id: str
    probabilities: dict[str, float]
    shifts: dict[str, float]           # sensor -> shift in fleet standard deviations
    signatures: dict[str, dict[str, int]]
    note: str | None = None


def discriminating(pack: dict, modes: list[str]) -> dict[str, dict[str, int]]:
    """mode -> {sensor: +1/-1/0} for sensors whose direction differs across the given modes."""
    out = {m: {} for m in modes}
    for sensor, spec in pack["sensors"].items():
        directions = [SIGN[spec["direction"][m]] for m in modes]
        if len(set(directions)) > 1:
            for mode, sign in zip(modes, directions):
                out[mode][sensor] = sign
    return out


@dataclass
class SignatureMatcher:
    pack: dict
    stats: dict
    temperature: float = 1.0

    def shifts(self, cycles: pd.DataFrame, sensors: list[str]) -> pd.DataFrame:
        """Per asset: mean of the last RECENT_CYCLES cycles relative to its first BASELINE_CYCLES."""
        relative = relative_to_own_start(normalise(cycles, sensors, self.stats), sensors)
        return relative.groupby("asset_id", sort=False)[sensors].apply(lambda g: g.tail(RECENT_CYCLES).mean())

    def match(self, cycles: pd.DataFrame, variant: str) -> list[FaultModeMatch]:
        modes = self.pack["variants"][variant]
        signatures = discriminating(self.pack, modes)
        sensors = sorted({s for sig in signatures.values() for s in sig})
        results = []
        if not sensors:
            for asset in cycles["asset_id"].unique():
                results.append(FaultModeMatch(asset, {modes[0]: 1.0}, {}, signatures,
                                              f"variant {variant} has one failure mode in the asset pack"))
            return results
        lengths = cycles.groupby("asset_id")["cycle"].count()
        for asset, shift in self.shifts(cycles, sensors).iterrows():
            scores = np.array([sum(sig[s] * shift[s] for s in sensors) / len(sensors) for sig in signatures.values()])
            weights = np.exp((scores - scores.max()) / self.temperature)
            probabilities = {m: round(float(p), 3) for m, p in zip(modes, weights / weights.sum())}
            note = (f"only {lengths[asset]} cycles; baseline and recent windows overlap"
                    if lengths[asset] < BASELINE_CYCLES + RECENT_CYCLES else None)
            results.append(FaultModeMatch(asset, probabilities, {s: round(float(shift[s]), 3) for s in sensors},
                                          signatures, note))
        return results

    def save(self, directory: Path) -> None:
        (directory / "fault_mode.json").write_text(json.dumps({"temperature": self.temperature}, indent=1),
                                                   encoding="utf-8")

    @classmethod
    def load(cls, directory: Path, pack: dict, stats: dict) -> "SignatureMatcher":
        meta = json.loads((directory / "fault_mode.json").read_text(encoding="utf-8"))
        return cls(pack=pack, stats=stats, **meta)
