"""Seeded impossible input data. Each fault corrupts what the snapshot returns for one engine; the run must
finish, and the data-quality check must report the expected severity (serious: escalation and low confidence;
minor: flagged in the brief only)."""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from tools.data_access import QueryResult, SnapshotSource


@dataclass
class DataFault:
    expected: str                                              # "serious" or "minor"
    cycles: Callable[[pd.DataFrame], pd.DataFrame] | None = None
    history: Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame] | None = None
    parts: Callable[[pd.DataFrame], pd.DataFrame] | None = None


def _at(frame: pd.DataFrame, rows, column: str, value) -> pd.DataFrame:
    frame = frame.copy()
    frame.loc[frame.index[rows], column] = value
    return frame


def _mid(frame: pd.DataFrame) -> int:
    return len(frame) // 2


def _removal(history: pd.DataFrame, cycles: pd.DataFrame) -> pd.DataFrame:
    row = {c: None for c in history.columns}
    row.update(wo_id="WO-FAKE-1", wo_type="unscheduled_removal", cycle_at=int(cycles.cycle.max()) - 5,
               component="hpc", description="Engine removed", severity="severe")
    return pd.concat([history, pd.DataFrame([row])], ignore_index=True)


def _future_order(history: pd.DataFrame, cycles: pd.DataFrame) -> pd.DataFrame:
    row = {c: None for c in history.columns}
    row.update(wo_id="WO-FAKE-2", wo_type="inspection", cycle_at=int(cycles.cycle.max()) + 40,
               component="hpc", description="Borescope inspection", severity="none")
    return pd.concat([history, pd.DataFrame([row])], ignore_index=True)


DATA_FAULTS: dict[str, DataFault] = {
    "spike_t30": DataFault("serious", cycles=lambda f: _at(f, _mid(f), "s_3", f["s_3"].iloc[_mid(f)] + 300)),
    "p30_in_kpa": DataFault("serious", cycles=lambda f: f.assign(s_7=f["s_7"] * 6.895)),
    "frozen_ps30": DataFault("serious", cycles=lambda f: _at(f, slice(-30, None), "s_11", f["s_11"].iloc[-30])),
    "missing_phi": DataFault("serious", cycles=lambda f: _at(f, slice(-5, None), "s_12", np.nan)),
    "duplicated_cycles": DataFault("serious", cycles=lambda f: pd.concat([f, f.tail(5)], ignore_index=True)),
    "cycles_out_of_order": DataFault("serious", cycles=lambda f: pd.concat([f.iloc[:-10], f.iloc[-10:][::-1]],
                                                                           ignore_index=True)),
    "telemetry_truncated": DataFault("serious", cycles=lambda f: f.iloc[:-20].reset_index(drop=True)),
    "removal_on_active_engine": DataFault("serious", history=_removal),
    "negative_stock": DataFault("serious", parts=lambda p: _at(p, 0, "qty_on_hand", -2) if len(p) else p),
    "cycle_gap": DataFault("minor", cycles=lambda f: f.drop(f.index[_mid(f):_mid(f) + 5]).reset_index(drop=True)),
    "t2_out_of_range": DataFault("minor", cycles=lambda f: _at(f, -1, "s_1", 600.0)),
    "work_order_after_last_cycle": DataFault("minor", history=_future_order),
}


class CorruptSource(SnapshotSource):
    """Snapshot data with one fault applied to one engine (and to the parts table, for stock faults)."""

    def __init__(self, asset_id: str, fault: DataFault):
        super().__init__()
        self.asset_id, self.fault = asset_id, fault

    def cycles(self, asset_id: str) -> QueryResult:
        result = super().cycles(asset_id)
        if asset_id == self.asset_id and self.fault.cycles:
            result.frame = self.fault.cycles(result.frame.reset_index(drop=True))
        return result

    def history(self, asset_id: str) -> QueryResult:
        result = super().history(asset_id)
        if asset_id == self.asset_id and self.fault.history:
            result.frame = self.fault.history(result.frame, super().cycles(asset_id).frame)
        return result

    def parts(self, part_numbers: list[str]) -> QueryResult:
        result = super().parts(part_numbers)
        if self.fault.parts:
            result.frame = self.fault.parts(result.frame.reset_index(drop=True))
        return result
