// Maintenance domain on top of the vendored Meterless engines.
// World Model: asset type -> component -> sensor -> failure mode -> task -> part, plus case events.
// H-MEM: past maintenance cases (signature, decision, outcome) with trust scores.

import fs from "node:fs";
import path from "node:path";
import { HMEM } from "../vendor/hmem/src/index.ts";
import type { FeedbackKind, MemoryLayer, MemoryRecord, MemoryType, ProvenanceOrigin } from "../vendor/hmem/src/index.ts";
import { WorldModel, canonicalJson, externalId } from "../vendor/world-model/src/index.ts";
import type { Entity, Fact, Provenance, Relationship, WorldEvent } from "../vendor/world-model/src/index.ts";

export class BadRequest extends Error {}
export class NotFound extends Error {}

export type SourceInput = { kind: string; by?: string; url?: string; runId?: string; at?: string };
export type EntityInput = { type: string; key: string; name?: string; attrs?: Record<string, unknown> };
export type RelationInput = { from: string; type: string; to: string; context?: string; attrs?: Record<string, unknown> };
export type FactInput = { about: string; predicate: string; value: unknown; confidence?: number };
export type WorldBatch = {
  system: string;
  source: SourceInput;
  entities?: EntityInput[];
  relations?: RelationInput[];
  facts?: FactInput[];
};
export type WorldWriteResult = {
  entities: { created: number; updated: number; unchanged: number };
  relations: { created: number; unchanged: number };
  facts: { created: number; unchanged: number };
};

export type MemoryInput = {
  content: string;
  source: string;
  tags?: string[];
  confidence?: number;
  layer?: MemoryLayer;
  type?: MemoryType;
  provenance?: { origin: ProvenanceOrigin; label: string; refs?: string[] };
  goalRunId?: string;
};
export type MemorySearch = { query: string; topN?: number; threshold?: number; tags?: string[] };
export type PublicMemory = Omit<MemoryRecord, "embedding">;

export type EngineOptions = {
  stateDir?: string;
  logFile?: string;
  namespace?: string;
  clock?: () => number;
};

const MAX_BATCH = 5000;

function publicMemory(m: MemoryRecord): PublicMemory {
  const { embedding: _embedding, ...rest } = m;
  return rest;
}

function need<T>(value: T | undefined | null, message: string): T {
  if (value === undefined || value === null || value === "") throw new BadRequest(message);
  return value;
}

export class ContextEngines {
  readonly world: WorldModel;
  readonly hmem: HMEM;
  private clock: () => number;
  private logFile?: string;
  private edgeCache?: { events: number; edges: Relationship[] };

  constructor(opts: EngineOptions = {}) {
    this.clock = opts.clock ?? Date.now;
    const clockDate = () => new Date(this.clock());
    this.world = opts.stateDir
      ? new WorldModel({ namespace: opts.namespace ?? "maintenance", storage: "local", directory: opts.stateDir, clock: clockDate })
      : new WorldModel({ namespace: opts.namespace ?? "maintenance", storage: "memory", clock: clockDate });
    this.hmem = new HMEM({
      clock: this.clock,
      actor: "context-service",
      persistDir: opts.stateDir ? path.join(opts.stateDir, "hmem") : undefined,
    });
    if (opts.logFile) {
      fs.mkdirSync(path.dirname(opts.logFile), { recursive: true });
      this.logFile = opts.logFile;
    }
  }

  // ------------------------------------------------------------ world model

  entityId(type: string, system: string, key: string): string {
    return externalId(type, system, key);
  }

  async writeWorld(batch: WorldBatch): Promise<WorldWriteResult> {
    const system = need(batch.system, "system is required");
    const source = this.provenance(batch.source);
    const entities = batch.entities ?? [];
    const relations = batch.relations ?? [];
    const facts = batch.facts ?? [];
    if (entities.length + relations.length + facts.length > MAX_BATCH) {
      throw new BadRequest(`batch too large (max ${MAX_BATCH} records)`);
    }
    this.checkSingleValued(relations);

    const graph = this.world.view("graph");
    const result: WorldWriteResult = {
      entities: { created: 0, updated: 0, unchanged: 0 },
      relations: { created: 0, unchanged: 0 },
      facts: { created: 0, unchanged: 0 },
    };

    for (const e of entities) {
      need(e.type, "entity.type is required");
      need(e.key, "entity.key is required");
      const existing = graph.entity(this.entityId(e.type, system, e.key));
      if (existing && this.sameEntity(existing, e)) {
        result.entities.unchanged += 1;
        continue;
      }
      await this.world.upsertEntity({ type: e.type, externalKey: { system, id: e.key }, name: e.name, attrs: e.attrs ?? {}, source });
      result.entities[existing ? "updated" : "created"] += 1;
    }

    for (const r of relations) {
      for (const end of [r.from, r.to]) {
        if (!graph.entity(need(end, "relation endpoints are required"))) {
          throw new BadRequest(`relation ${r.from} -[${r.type}]-> ${r.to}: unknown entity ${end}`);
        }
      }
      const before = this.world.events().length;
      await this.world.relate({ from: r.from, type: need(r.type, "relation.type is required"), to: r.to, context: r.context, attrs: r.attrs, source });
      result.relations[this.world.events().length > before ? "created" : "unchanged"] += 1;
    }

    for (const f of facts) {
      if (!graph.entity(need(f.about, "fact.about is required"))) throw new BadRequest(`fact about unknown entity ${f.about}`);
      const before = this.world.events().length;
      await this.world.assertFact({ about: f.about, predicate: need(f.predicate, "fact.predicate is required"), value: f.value, confidence: f.confidence, source });
      result.facts[this.world.events().length > before ? "created" : "unchanged"] += 1;
    }

    this.log("world.write", { system, source: batch.source.kind, by: batch.source.by, runId: batch.source.runId, ...result });
    return result;
  }

  queryEntity(id: string): {
    entity: Entity;
    outgoing: { type: string; to: Entity; attrs?: Record<string, unknown> }[];
    incoming: { type: string; from: Entity; attrs?: Record<string, unknown> }[];
    facts: Fact[];
  } {
    const graph = this.world.view("graph");
    const entity = graph.entity(id);
    if (!entity) throw new NotFound(`unknown entity ${id}`);
    const edges = this.openEdges();
    return {
      entity,
      outgoing: edges.filter((e) => e.from === entity.id).map((e) => ({ type: e.type, to: graph.entity(e.to)!, attrs: e.attrs })),
      incoming: edges.filter((e) => e.to === entity.id).map((e) => ({ type: e.type, from: graph.entity(e.from)!, attrs: e.attrs })),
      facts: this.world.view("facts").about(entity.id),
    };
  }

  listEntities(type: string, where: Record<string, unknown> = {}): Entity[] {
    return this.world
      .view("graph")
      .query({ type, where: (e) => Object.entries(where).every(([k, v]) => canonicalJson(e.attrs[k]) === canonicalJson(v)) })
      .sort((a, b) => a.id.localeCompare(b.id));
  }

  worldAudit(limit = 100) {
    return this.world.audit().slice(-limit);
  }

  // Edges are single-valued per (from, type, context): a second target closes the first.
  // One-to-many links (task -> parts, failure mode -> signature) belong in entity attrs.
  private checkSingleValued(relations: RelationInput[]): void {
    const seen = new Map<string, string>();
    for (const r of relations) {
      const slot = `${r.from}|${r.type}|${r.context ?? ""}`;
      const other = seen.get(slot);
      if (other !== undefined && other !== r.to) {
        throw new BadRequest(`${r.from} -[${r.type}]-> has two targets (${other}, ${r.to}); World Model edges are single-valued, use a context or entity attrs`);
      }
      seen.set(slot, r.to);
    }
  }

  private openEdges(): Relationship[] {
    const events = this.world.events();
    if (this.edgeCache?.events === events.length) return this.edgeCache.edges;
    const edges = new Map<string, Relationship>();
    for (const ev of events as readonly WorldEvent[]) {
      if (ev.kind !== "relate") continue;
      const r = ev.payload as Relationship;
      for (const [id, other] of edges) {
        if (other.from === r.from && other.type === r.type && (other.context ?? "") === (r.context ?? "") && other.to !== r.to) edges.delete(id);
      }
      edges.set(r.id, r);
    }
    const list = [...edges.values()].filter((e) => !e.validTo);
    this.edgeCache = { events: events.length, edges: list };
    return list;
  }

  private sameEntity(existing: Entity, input: EntityInput): boolean {
    if (existing.type !== input.type) return false;
    if (input.name !== undefined && existing.name !== input.name) return false;
    return Object.entries(input.attrs ?? {}).every(([k, v]) => canonicalJson(existing.attrs[k]) === canonicalJson(v));
  }

  private provenance(source: SourceInput | undefined): Provenance {
    const s = need(source, "source is required (kind, by, at)");
    return { kind: need(s.kind, "source.kind is required"), by: s.by, url: s.url, runId: s.runId, at: s.at ?? new Date(this.clock()).toISOString() };
  }

  // ------------------------------------------------------------------ H-MEM

  writeMemory(input: MemoryInput): { memory: PublicMemory; created: boolean } {
    need(input.content, "content is required");
    const source = need(input.source, "source is required");
    const existing = this.hmem.store.all().find((m) => m.source === source && !m.supersededBy);
    if (existing) return { memory: publicMemory(existing), created: false };
    const memory = this.hmem.add({
      content: input.content,
      source,
      tags: input.tags,
      confidence: input.confidence,
      layer: input.layer ?? "long_term",
      type: input.type ?? "factual",
      goalRunId: input.goalRunId,
      provenance: input.provenance && { ...input.provenance, learnedAt: this.clock() },
    });
    this.log("mem.write", { id: memory.id, source, tags: memory.tags });
    return { memory: publicMemory(memory), created: true };
  }

  searchMemory(search: MemorySearch) {
    const query = need(search.query, "query is required");
    const tags = search.tags ?? [];
    if (tags.length === 0) {
      const result = this.hmem.query(query, { topN: search.topN, threshold: search.threshold });
      return { ...result, memories: result.memories.map((r) => ({ memory: publicMemory(r.memory), relevance: r.relevance })) };
    }
    const threshold = search.threshold ?? 0.35;
    const scored = this.hmem.store
      .all()
      .filter((m) => tags.every((t) => m.tags.includes(t)))
      .map((memory) => ({ memory, relevance: this.hmem.retrieval.scoreOne(memory, query) }))
      .filter((r) => r.relevance >= threshold)
      .sort((a, b) => b.relevance - a.relevance || a.memory.id.localeCompare(b.memory.id))
      .slice(0, search.topN ?? 5);
    for (const { memory } of scored) {
      this.hmem.store.touch(memory.id);
      this.hmem.ledger.log(memory.id, "read", "context-service", { details: { query, tags } });
    }
    return {
      memories: scored.map((r) => ({ memory: publicMemory(r.memory), relevance: r.relevance })),
      context: this.hmem.retrieval.formatReinjection(scored),
      trace: {
        retrievalReason: `hybrid ranking for "${query}" within tags [${tags.join(", ")}]`,
        strategy: scored.length <= 1 ? "minimal" : "comprehensive",
        scores: Object.fromEntries(scored.map((r) => [r.memory.id, Number(r.relevance.toFixed(4))])),
      },
    };
  }

  feedback(id: string, kind: FeedbackKind): PublicMemory {
    if (!["helpful", "not_helpful", "wrong"].includes(kind)) throw new BadRequest(`unknown feedback kind ${kind}`);
    if (!this.hmem.store.get(id)) throw new NotFound(`unknown memory ${id}`);
    this.hmem.feedback(id, kind);
    const memory = this.hmem.store.get(id)!;
    this.log("mem.feedback", { id, kind, confidence: memory.confidence });
    return publicMemory(memory);
  }

  memoryLedger(memoryId?: string, limit = 100) {
    return (memoryId ? this.hmem.ledger.history(memoryId) : this.hmem.ledger.all()).slice(-limit);
  }

  // ----------------------------------------------------------------- status

  health() {
    const graph = this.world.view("graph");
    const types: Record<string, number> = {};
    for (const e of graph.query({})) types[e.type] = (types[e.type] ?? 0) + 1;
    return {
      status: "ok",
      engines: { world_model: "meterless-reference", hmem: "meterless-reference" },
      world: { entities: types, relations: this.openEdges().length, events: this.world.events().length },
      hmem: { memories: this.hmem.store.all().length, ledger: this.hmem.ledger.stats() },
    };
  }

  private log(op: string, details: Record<string, unknown>): void {
    if (!this.logFile) return;
    fs.appendFileSync(this.logFile, JSON.stringify({ at: new Date(this.clock()).toISOString(), op, ...details }) + "\n", "utf-8");
  }
}
