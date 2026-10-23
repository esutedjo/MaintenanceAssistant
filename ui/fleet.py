"""Fleet listing and cycle series for the UI. The table and the driving-sensor chart both read
the committed snapshot — scoring 200 engines live is too slow, and a Zetaris round-trip after the
run would add seconds to the brief."""

from __future__ import annotations

import pandas as pd

from settings import Settings
from tools.data_access import SNAPSHOT_DIR, SnapshotSource


FLEET_COLUMNS = ["asset_id", "dataset", "esn", "last_cycle", "last_seen"]


def list_active_assets(directory=SNAPSHOT_DIR) -> pd.DataFrame:
    frame = pd.read_parquet(directory / "assets.parquet")
    active = frame.loc[frame["fleet_status"] == "active", FLEET_COLUMNS].copy()
    return active.sort_values("asset_id").reset_index(drop=True)


def load_cycles(asset_id: str, _settings: Settings | None = None) -> pd.DataFrame:
    return SnapshotSource().cycles(asset_id).frame
