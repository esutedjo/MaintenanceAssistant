"""Plausibility limits per sensor for the data-quality check, from the historical (run-to-failure) engines:
minimum, maximum and the largest cycle-to-cycle step. Written into the asset pack, so the check needs no raw
data at run time. Rebuild with `uv run task sensor-ranges` after `uv run task download`."""

import json
import sys

import pandas as pd

from data.db import PACK_DIR, load_pack
from data.fleet import DATASETS, SENSORS, read_raw


def build(frames: list[pd.DataFrame]) -> dict:
    ranges = {}
    for sensor in SENSORS:
        values = pd.concat([f[sensor] for f in frames])
        steps = pd.concat([f.groupby("unit")[sensor].diff().abs() for f in frames]).dropna()
        ranges[sensor] = {"min": round(float(values.min()), 4), "max": round(float(values.max()), 4),
                          "max_step": round(float(steps.max()), 4)}
    return ranges


def main() -> int:
    pack = load_pack()
    target = PACK_DIR / pack["data_quality"]["ranges_file"]
    ranges = build([read_raw(d, "train") for d in DATASETS])
    doc = {"source": f"C-MAPSS {', '.join(DATASETS)} training units (run to failure)", "sensors": ranges}
    target.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(ranges)} sensor ranges to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
