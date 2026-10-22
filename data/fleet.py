"""Turns raw C-MAPSS files into a synthetic fleet: training units are historical engines removed at failure,
test units are the active fleet whose last observed cycle is the reference date."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

RAW_DIR = Path(__file__).resolve().parent / "raw" / "cmapss"
DATASETS = ("FD001", "FD003")
ASSET_TYPE = "turbofan_cmapss"
REFERENCE_TIME = datetime(2026, 10, 22, tzinfo=timezone.utc)
SEED = 20261022
SENSORS = [f"s_{i}" for i in range(1, 22)]
COLUMNS = ["unit", "cycle", "setting_1", "setting_2", "setting_3", *SENSORS]
SPLITS = {"train": "H", "test": "A"}


def read_raw(dataset: str, split: str, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    return pd.read_csv(raw_dir / f"{split}_{dataset}.txt", sep=r"\s+", header=None, names=COLUMNS)


def read_true_rul(dataset: str, raw_dir: Path = RAW_DIR) -> pd.Series:
    rul = pd.read_csv(raw_dir / f"RUL_{dataset}.txt", header=None).iloc[:, 0]
    rul.index = rul.index + 1
    return rul


def asset_id(dataset: str, split: str, unit: int) -> str:
    return f"{dataset}-{SPLITS[split]}{int(unit):03d}"


def proxy_fault_mode(frame: pd.DataFrame, dataset: str) -> pd.Series:
    """Proxy label per unit. FD001 is HPC only; in FD003 fan degradation drives phi (s_12) up, HPC drives it down."""
    units = frame["unit"].unique()
    if dataset == "FD001":
        return pd.Series("hpc_degradation", index=units)
    grouped = frame.groupby("unit")["s_12"]
    delta = grouped.apply(lambda s: s.tail(15).mean() - s.head(30).mean())
    return delta.map(lambda d: "fan_degradation" if d > 0 else "hpc_degradation")


def operating_regime(frame: pd.DataFrame) -> pd.Series:
    key = frame[["setting_1", "setting_2", "setting_3"]].round(0).astype(int).astype(str).agg("/".join, axis=1)
    codes = {k: f"R{i + 1}" for i, k in enumerate(sorted(key.unique()))}
    return key.map(codes)


def build_fleet(cycles_per_day: int, raw_dir: Path = RAW_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (assets, cycles). Cycle timestamps are spaced 24/cycles_per_day hours apart."""
    rng = np.random.default_rng(SEED)
    step = timedelta(hours=24 / cycles_per_day)
    assets, cycles = [], []

    for dataset in DATASETS:
        true_rul = read_true_rul(dataset, raw_dir)
        for split in ("train", "test"):
            raw = read_raw(dataset, split, raw_dir)
            labels = proxy_fault_mode(raw, dataset)
            last_cycle = raw.groupby("unit")["cycle"].max()
            for unit, last in last_cycle.items():
                if split == "train":
                    end = REFERENCE_TIME - timedelta(days=int(rng.integers(30, 900)))
                    status, rul = "removed", 0
                else:
                    end = REFERENCE_TIME
                    status, rul = "active", int(true_rul[unit])
                assets.append(
                    {
                        "asset_id": asset_id(dataset, split, unit),
                        "asset_type": ASSET_TYPE,
                        "dataset": dataset,
                        "source_split": split,
                        "source_unit": int(unit),
                        "fleet_status": status,
                        "last_cycle": int(last),
                        "last_seen": end,
                        "proxy_fault_mode": labels[unit],
                        "true_rul_at_last_cycle": rul,
                    }
                )
            raw = raw.assign(
                asset_id=[asset_id(dataset, split, u) for u in raw["unit"]],
                asset_type=ASSET_TYPE,
                dataset=dataset,
                operating_regime=operating_regime(raw),
            )
            ends = {a["asset_id"]: a["last_seen"] for a in assets}
            raw["ts"] = [ends[a] - (last_cycle[u] - c) * step for a, u, c in zip(raw["asset_id"], raw["unit"], raw["cycle"])]
            cycles.append(raw)

    frame = pd.concat(cycles, ignore_index=True)
    ordered = ["asset_id", "asset_type", "dataset", "cycle", "ts", "operating_regime", "setting_1", "setting_2", "setting_3", *SENSORS]
    return pd.DataFrame(assets), frame[ordered]
