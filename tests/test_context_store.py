import json

import httpx
import pytest

from casefile.context_store import (
    EntityIn, MemoryIn, MeterlessStore, RelationIn, Source, SqliteStore, WorldBatch, entity_id, open_context_store,
)
from settings import Settings

SYSTEM = "turbofan_cmapss"
HPC = entity_id("component", SYSTEM, "hpc")
S11 = entity_id("sensor", SYSTEM, "s_11")


def pack_batch() -> WorldBatch:
    return WorldBatch(
        system=SYSTEM,
        source=Source(kind="file", by="test", at="2026-10-22T00:00:00Z"),
        entities=[EntityIn(type="component", key="hpc", name="HPC"),
                  EntityIn(type="component", key="fan", name="Fan"),
                  EntityIn(type="sensor", key="s_11", name="Ps30", attrs={"unit": "psia"})],
        relations=[RelationIn(**{"from": S11, "type": "monitors", "to": HPC})],
    )


@pytest.fixture
def store():
    s = SqliteStore(":memory:")
    yield s
    s.close()


def test_sqlite_world_load_is_idempotent(store):
    first = store.write_world(pack_batch())
    assert first["entities"] == {"created": 3, "updated": 0, "unchanged": 0}
    second = store.write_world(pack_batch())
    assert second["entities"] == {"created": 0, "updated": 0, "unchanged": 3}
    assert second["relations"] == {"created": 0, "unchanged": 1}
    result = store.query_entity(HPC)
    assert [(e["type"], e["from"]["id"]) for e in result["incoming"]] == [("monitors", S11)]


def test_sqlite_rejects_dangling_and_multi_target_edges(store):
    store.write_world(pack_batch())
    batch = pack_batch()
    batch.relations = [RelationIn(**{"from": S11, "type": "monitors", "to": entity_id("component", SYSTEM, "lpt")})]
    with pytest.raises(ValueError, match="unknown entity"):
        store.write_world(batch)
    batch.relations = [RelationIn(**{"from": S11, "type": "monitors", "to": HPC}),
                       RelationIn(**{"from": S11, "type": "monitors", "to": entity_id("component", SYSTEM, "fan")})]
    with pytest.raises(ValueError, match="two targets"):
        store.write_world(batch)


def test_sqlite_memory_search_feedback(store):
    cases = [MemoryIn(content="FD003-H012 HPC degradation, Ps30 rising, removed at cycle 212", source="cmms:WO-1",
                      tags=["case", "hpc_degradation"], confidence=0.8),
             MemoryIn(content="FD003-H040 fan degradation, phi rising, removed at cycle 260", source="cmms:WO-2",
                      tags=["case", "fan_degradation"], confidence=0.8)]
    assert store.write_memories(cases)["created"] == 2
    assert store.write_memories(cases[:1])["created"] == 0
    hits = store.search_memory("HPC degradation Ps30 rising")
    assert hits[0].source == "cmms:WO-1"
    assert [h.source for h in store.search_memory("HPC degradation", tags=["fan_degradation"], threshold=0)] == ["cmms:WO-2"]
    assert store.feedback(hits[0].id, "wrong") == pytest.approx(0.6)
    assert [h.id for h in store.search_memory("HPC degradation", tags=["review"], threshold=0)] == [hits[0].id]


def test_meterless_store_maps_requests_and_errors():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"null")
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/world/query":
            return httpx.Response(404, json={"error": "unknown entity x"})
        if request.url.path == "/mem/search":
            memory = {"id": "mem-1", "content": "c", "source": "cmms:WO-1", "tags": ["case"], "confidence": 0.8}
            return httpx.Response(200, json={"memories": [{"memory": memory, "relevance": 0.5}], "context": "", "trace": {}})
        return httpx.Response(400, json={"error": "source is required"})

    store = MeterlessStore("http://context", transport=httpx.MockTransport(handler))
    assert store.search_memory("hpc", tags=["case"])[0].relevance == 0.5
    assert seen[0][2] == {"query": "hpc", "topN": 5, "tags": ["case"]}
    with pytest.raises(KeyError):
        store.query_entity("x")
    with pytest.raises(ValueError, match="source is required"):
        store.write_world(pack_batch())
    assert seen[-1][2]["relations"][0]["from"] == S11


def test_open_context_store_falls_back_to_sqlite(tmp_path):
    settings = Settings(_env_file=None, context_store="meterless", context_service_url="http://127.0.0.1:9",
                        context_sqlite_path=tmp_path / "ctx.sqlite")
    store = open_context_store(settings)
    assert store.backend == "sqlite"
    assert "unreachable" in store.degraded_reason
