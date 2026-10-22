"""Load the shared context: asset pack + FMEA into the World Model, past removals into H-MEM.
Run: uv run task load-context   (start context-service first: cd context-service; npm start)"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from casefile.context_store import (
    ContextStore, EntityIn, MemoryIn, MemoryProvenance, RelationIn, Source, WorldBatch, entity_id, open_context_store,
)
from data.db import PACK_DIR, load_pack
from settings import ROOT, Settings, get_settings

PARTS_CSV = ROOT / "data" / "seed" / "parts.csv"
CASE_CONFIDENCE = 0.8

REMOVALS_SQL = (
    "SELECT w.wo_id, w.asset_id, w.cycle_at, w.component, f.finding_id, f.failure_mode, f.note "
    "FROM cmms_pg.work_orders w JOIN cmms_pg.findings f ON f.wo_id = w.wo_id "
    "WHERE w.wo_type = 'unscheduled_removal'"
)
FIRST_WARNING_SQL = (
    "SELECT asset_id, MIN(cycle_at) AS first_warning FROM cmms_pg.findings "
    "WHERE severity IN ('moderate', 'severe') GROUP BY asset_id"
)


def build_world_batch(pack: dict, fmea: pd.DataFrame, parts: pd.DataFrame) -> WorldBatch:
    """Asset structure only. One-to-many links (signatures, task parts) are attrs: World Model edges are single-valued."""
    system = pack["asset_type"]
    ref = lambda type_, key: entity_id(type_, system, key)  # noqa: E731
    asset_type = ref("asset_type", system)
    entities = [EntityIn(type="asset_type", key=system, name=pack["description"].split(".")[0],
                         attrs={"pack_version": pack["version"], "source": pack["source"]})]
    relations = []

    for key, label in pack["components"].items():
        entities.append(EntityIn(type="component", key=key, name=label))
        relations.append(RelationIn(**{"from": ref("component", key), "type": "part_of", "to": asset_type}))

    for code, s in pack["sensors"].items():
        entities.append(EntityIn(type="sensor", key=code, name=s["name"],
                                 attrs={"unit": s["unit"], "description": s["description"], "direction": s["direction"]}))
        relations.append(RelationIn(**{"from": ref("sensor", code), "type": "monitors", "to": ref("component", s["component"])}))

    modes = list(pack["failure_modes"])
    for mode, fm in pack["failure_modes"].items():
        moving = {c: s for c, s in pack["sensors"].items() if s["direction"][mode] != "none"}
        signature = [{"sensor": c, "name": s["name"], "direction": s["direction"][mode]} for c, s in moving.items()]
        discriminating = [c for c, s in moving.items() if len({s["direction"][m] for m in modes}) > 1]
        entities.append(EntityIn(type="failure_mode", key=mode, name=fm["label"],
                                 attrs={"signature": signature, "discriminating_sensors": discriminating}))
        relations.append(RelationIn(**{"from": ref("failure_mode", mode), "type": "affects", "to": ref("component", fm["component"])}))

    for fmea_id, rows in fmea.groupby("fmea_id", sort=True):
        first = rows.iloc[0]
        task_parts = [{"part_number": r.part_number, "qty": int(r.qty)} for r in rows.itertuples() if pd.notna(r.part_number)]
        entities.append(EntityIn(type="task", key=fmea_id, name=first["task"], attrs={
            "task_type": first["task_type"], "trigger_severity": first["trigger_severity"], "cause": first["cause"],
            "effect": first["effect"], "detection": first["detection"], "parts": task_parts}))
        relations.append(RelationIn(**{"from": ref("task", fmea_id), "type": "addresses", "to": ref("failure_mode", first["failure_mode"])}))

    for p in parts.itertuples():
        entities.append(EntityIn(type="part", key=p.part_number, name=p.description))
        relations.append(RelationIn(**{"from": ref("part", p.part_number), "type": "fits", "to": ref("component", p.component)}))

    source = Source(kind="file", by="load-context", url=f"asset_packs/{system}/manifest.yaml@{pack['version']}")
    return WorldBatch(system=system, source=source, entities=entities, relations=relations)


def removals_live(settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    from tools.zetaris import ZetarisClient, rows_to_frame

    client = ZetarisClient(settings)
    try:
        removals = rows_to_frame(client.run_select(REMOVALS_SQL))
        warnings = rows_to_frame(client.run_select(FIRST_WARNING_SQL))
    finally:
        client.close()
    removals["cycle_at"] = removals["cycle_at"].astype(int)
    warnings["first_warning"] = warnings["first_warning"].astype(int)
    return removals, warnings


def removals_snapshot(pack: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Regenerates the same seeded cmms history locally (needs `uv run task download`)."""
    from data.fleet import SEED, build_fleet
    from data.generate_cmms import build_history

    assets, _ = build_fleet(pack["thresholds"]["cycles_per_day"])
    history = build_history(assets, pack["thresholds"]["cycles_per_day"], np.random.default_rng(SEED + 1))
    wo, fn = history["work_orders"], history["findings"]
    removals = wo[wo.wo_type == "unscheduled_removal"].merge(fn[["wo_id", "finding_id", "failure_mode", "note"]], on="wo_id")
    warnings = (fn[fn.severity.isin(["moderate", "severe"])].groupby("asset_id", as_index=False)["cycle_at"].min()
                .rename(columns={"cycle_at": "first_warning"}))
    return removals[["wo_id", "asset_id", "cycle_at", "component", "finding_id", "failure_mode", "note"]], warnings


def case_memories(pack: dict, removals: pd.DataFrame, warnings: pd.DataFrame) -> list[MemoryIn]:
    labels = {m: fm["label"] for m, fm in pack["failure_modes"].items()}
    frame = removals.merge(warnings, on="asset_id", how="left").sort_values("wo_id")
    memories = []
    for r in frame.itertuples():
        dataset = r.asset_id.split("-")[0]
        warning = ("no moderate finding before removal" if pd.isna(r.first_warning)
                   else f"first moderate finding at cycle {int(r.first_warning)}, {r.cycle_at - int(r.first_warning)} cycles before removal")
        content = (f"Past case {r.asset_id} ({dataset}, synthetic): {labels[r.failure_mode]}. "
                   f"Engine removed unscheduled at cycle {r.cycle_at}; {warning}. Shop finding: {r.note}")
        memories.append(MemoryIn(
            content=content, source=f"cmms:work_orders/{r.wo_id}", confidence=CASE_CONFIDENCE,
            tags=["case", pack["asset_type"], r.failure_mode, r.component, dataset],
            provenance=MemoryProvenance(origin="document", label=f"Removal work order {r.wo_id}",
                                        refs=[f"cmms_pg.work_orders#{r.wo_id}", f"cmms_pg.findings#{r.finding_id}"]),
        ))
    return memories


def load(store: ContextStore, settings: Settings, pack_dir: Path = PACK_DIR) -> dict:
    pack = load_pack(pack_dir)
    fmea = pd.read_csv(pack_dir / pack["failure_modes_csv"], dtype={"qty": "Int64"})
    world = store.write_world(build_world_batch(pack, fmea, pd.read_csv(PARTS_CSV)))
    removals, warnings = removals_live(settings) if settings.zetaris_mode == "live" else removals_snapshot(pack)
    memories = store.write_memories(case_memories(pack, removals, warnings))
    return {"world": world, "memories": {k: v for k, v in memories.items() if k != "ids"}}


def main() -> int:
    settings = get_settings()
    store = open_context_store(settings)
    if store.degraded_reason:
        print(f"WARNING: {store.degraded_reason}")
    result = load(store, settings)
    print(f"context [{store.backend}, zetaris {settings.zetaris_mode}]: {result}")
    print(store.health())
    return 0


if __name__ == "__main__":
    sys.exit(main())
