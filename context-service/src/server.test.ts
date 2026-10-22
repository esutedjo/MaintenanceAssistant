import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { after, before, describe, it } from "node:test";
import { ContextEngines } from "./engines.ts";
import { createServer } from "./server.ts";

const SOURCE = { kind: "file", by: "test", at: "2026-10-22T00:00:00.000Z", url: "asset_packs/turbofan_cmapss/manifest.yaml" };
const PACK = {
  system: "turbofan_cmapss",
  source: SOURCE,
  entities: [
    { type: "asset_type", key: "turbofan_cmapss", name: "Turbofan (C-MAPSS)" },
    { type: "component", key: "hpc", name: "High-pressure compressor" },
    { type: "sensor", key: "s_11", name: "Ps30", attrs: { unit: "psia", direction: "up" } },
    { type: "failure_mode", key: "hpc_degradation", attrs: { signature: [{ sensor: "s_11", direction: "up" }] } },
    { type: "task", key: "FM-HPC-01", attrs: { parts: ["TF-GEN-8101"] } },
  ],
  relations: [
    { from: "component:turbofan_cmapss:hpc", type: "part_of", to: "asset_type:turbofan_cmapss:turbofan_cmapss" },
    { from: "sensor:turbofan_cmapss:s_11", type: "monitors", to: "component:turbofan_cmapss:hpc" },
    { from: "failure_mode:turbofan_cmapss:hpc_degradation", type: "affects", to: "component:turbofan_cmapss:hpc" },
    { from: "task:turbofan_cmapss:FM-HPC-01", type: "addresses", to: "failure_mode:turbofan_cmapss:hpc_degradation" },
  ],
};

let server: ReturnType<typeof createServer>;
let base: string;

async function call(method: string, route: string, body?: unknown) {
  const res = await fetch(base + route, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: res.status, body: (await res.json()) as any };
}

before(async () => {
  let t = Date.parse("2026-10-22T00:00:00Z");
  server = createServer(new ContextEngines({ clock: () => (t += 1000) }));
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(() => server.close());

describe("world model", () => {
  it("loads the pack and reloading changes nothing", async () => {
    const first = await call("POST", "/world/load", PACK);
    assert.equal(first.status, 200);
    assert.deepEqual(first.body.entities, { created: 5, updated: 0, unchanged: 0 });
    assert.deepEqual(first.body.relations, { created: 4, unchanged: 0 });
    const events = (await call("GET", "/health")).body.world.events;

    const second = await call("POST", "/world/load", PACK);
    assert.deepEqual(second.body.entities, { created: 0, updated: 0, unchanged: 5 });
    assert.deepEqual(second.body.relations, { created: 0, unchanged: 4 });
    assert.equal((await call("GET", "/health")).body.world.events, events);
  });

  it("returns edges in both directions", async () => {
    const { body } = await call("POST", "/world/query", { id: "component:turbofan_cmapss:hpc" });
    assert.deepEqual(body.outgoing.map((e: any) => [e.type, e.to.id]), [["part_of", "asset_type:turbofan_cmapss:turbofan_cmapss"]]);
    assert.deepEqual(body.incoming.map((e: any) => e.type).sort(), ["affects", "monitors"]);
  });

  it("filters entities by type and attrs", async () => {
    const { body } = await call("POST", "/world/query", { type: "sensor", where: { direction: "up" } });
    assert.deepEqual(body.entities.map((e: any) => e.id), ["sensor:turbofan_cmapss:s_11"]);
  });

  it("records case facts on an existing entity", async () => {
    await call("POST", "/world/event", { system: "maintenance_assistant", source: { kind: "agent", by: "test" }, entities: [{ type: "case", key: "CASE-1" }] });
    const fact = { about: "case:maintenance_assistant:CASE-1", predicate: "status", value: "approved", confidence: 1 };
    const res = await call("POST", "/world/event", { system: "maintenance_assistant", source: { kind: "agent", by: "engineer" }, facts: [fact] });
    assert.deepEqual(res.body.facts, { created: 1, unchanged: 0 });
    const { body } = await call("POST", "/world/query", { id: fact.about });
    assert.equal(body.entity.attrs.status, "approved");
  });

  it("rejects writes without provenance, dangling edges and multi-target edges", async () => {
    assert.equal((await call("POST", "/world/load", { system: "x", entities: [{ type: "a", key: "1" }] })).status, 400);
    const dangling = { ...PACK, entities: [], relations: [{ from: "sensor:turbofan_cmapss:s_11", type: "monitors", to: "component:turbofan_cmapss:fan" }] };
    assert.match((await call("POST", "/world/load", dangling)).body.error, /unknown entity/);
    const multi = { ...PACK, entities: [], relations: [PACK.relations[3], { ...PACK.relations[3], to: "component:turbofan_cmapss:hpc" }] };
    assert.match((await call("POST", "/world/load", multi)).body.error, /single-valued/);
  });
});

describe("h-mem", () => {
  const hpcCase = {
    content: "FD003-H012 HPC degradation: Ps30 and T50 rising, phi falling; engine removed at cycle 212.",
    source: "cmms:WO-000123",
    tags: ["case", "turbofan_cmapss", "hpc_degradation"],
    confidence: 0.8,
    provenance: { origin: "document", label: "work order WO-000123", refs: ["cmms:work_orders/WO-000123"] },
  };
  const fanCase = { ...hpcCase, content: "FD003-H040 fan degradation: phi rising, Nf drifting; engine removed at cycle 260.", source: "cmms:WO-000456", tags: ["case", "turbofan_cmapss", "fan_degradation"] };

  it("writes memories idempotently on source", async () => {
    const first = await call("POST", "/mem/write", { memories: [hpcCase, fanCase] });
    assert.equal(first.body.created, 2);
    const again = await call("POST", "/mem/write", hpcCase);
    assert.equal(again.body.created, false);
    assert.equal(again.body.memory.embedding, undefined);
  });

  it("searches with and without a tag filter", async () => {
    const all = await call("POST", "/mem/search", { query: "HPC degradation Ps30 rising", threshold: 0 });
    assert.equal(all.body.memories[0].memory.source, "cmms:WO-000123");
    const fan = await call("POST", "/mem/search", { query: "HPC degradation Ps30 rising", threshold: 0, tags: ["fan_degradation"] });
    assert.deepEqual(fan.body.memories.map((m: any) => m.memory.source), ["cmms:WO-000456"]);
  });

  it("applies feedback deltas and records them in the ledger", async () => {
    const id = (await call("POST", "/mem/write", hpcCase)).body.memory.id;
    const res = await call("POST", "/mem/feedback", { id, kind: "wrong" });
    assert.ok(Math.abs(res.body.memory.confidence - 0.6) < 1e-9);
    assert.ok(res.body.memory.tags.includes("review"));
    const ledger = await call("GET", `/mem/ledger?memory_id=${id}`);
    assert.deepEqual(ledger.body.entries.map((e: any) => e.action).filter((a: string) => a !== "read"), ["create", "feedback"]);
    assert.equal((await call("POST", "/mem/feedback", { id: "mem-999", kind: "helpful" })).status, 404);
  });
});
