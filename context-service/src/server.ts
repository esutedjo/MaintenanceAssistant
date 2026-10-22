// REST surface of the context service. Binds to localhost only; the Python ContextStore is the client.
//   GET  /health                      engine status and counts
//   POST /world/load                  idempotent batch of entities, relations, facts (asset pack + FMEA)
//   POST /world/event                 case events: same batch shape, usually facts about a case or asset
//   POST /world/query                 {id} -> entity, edges, facts | {type, where} -> entities
//   GET  /world/audit?limit=          World Model audit log
//   POST /mem/write                   one memory or {memories: [...]}; idempotent on source
//   POST /mem/search                  {query, topN, threshold, tags}
//   POST /mem/feedback                {id, kind: helpful | not_helpful | wrong}
//   GET  /mem/ledger?memory_id=&limit=  H-MEM trust ledger

import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { BadRequest, ContextEngines, NotFound } from "./engines.ts";
import type { MemoryInput } from "./engines.ts";

const MAX_BODY_BYTES = 5 * 1024 * 1024;

type Handler = (body: any, url: URL) => unknown | Promise<unknown>;

export function routes(engines: ContextEngines): Record<string, Handler> {
  const limit = (url: URL) => Number(url.searchParams.get("limit") ?? 100);
  return {
    "GET /health": () => engines.health(),
    "POST /world/load": (body) => engines.writeWorld(body),
    "POST /world/event": (body) => engines.writeWorld(body),
    "POST /world/query": (body) => {
      if (body?.id) return engines.queryEntity(body.id);
      if (body?.type) return { entities: engines.listEntities(body.type, body.where ?? {}) };
      throw new BadRequest("query needs id or type");
    },
    "GET /world/audit": (_body, url) => ({ entries: engines.worldAudit(limit(url)) }),
    "POST /mem/write": (body) => {
      if (Array.isArray(body?.memories)) {
        const results = (body.memories as MemoryInput[]).map((m) => engines.writeMemory(m));
        return { created: results.filter((r) => r.created).length, unchanged: results.filter((r) => !r.created).length, ids: results.map((r) => r.memory.id) };
      }
      return engines.writeMemory(body);
    },
    "POST /mem/search": (body) => engines.searchMemory(body ?? {}),
    "POST /mem/feedback": (body) => ({ memory: engines.feedback(body?.id, body?.kind) }),
    "GET /mem/ledger": (_body, url) => ({ entries: engines.memoryLedger(url.searchParams.get("memory_id") ?? undefined, limit(url)) }),
  };
}

function readBody(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new BadRequest(`body larger than ${MAX_BODY_BYTES} bytes`));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (size === 0) return resolve(undefined);
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf-8")));
      } catch {
        reject(new BadRequest("body is not valid JSON"));
      }
    });
    req.on("error", reject);
  });
}

export function createServer(engines: ContextEngines): http.Server {
  const table = routes(engines);
  return http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const handler = table[`${req.method} ${url.pathname}`];
    const send = (status: number, payload: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    };
    if (!handler) return send(404, { error: `no route ${req.method} ${url.pathname}` });
    try {
      send(200, await handler(await readBody(req), url));
    } catch (err) {
      const status = err instanceof BadRequest ? 400 : err instanceof NotFound ? 404 : 500;
      send(status, { error: err instanceof Error ? err.message : String(err) });
    }
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const port = Number(process.env.CONTEXT_SERVICE_PORT ?? 8710);
  const engines = new ContextEngines({
    stateDir: process.env.CONTEXT_STATE_DIR ?? path.join(root, ".state"),
    logFile: path.join(process.env.CONTEXT_LOG_DIR ?? path.join(root, "..", "logs"), "context-service.jsonl"),
  });
  createServer(engines).listen(port, "127.0.0.1", () => {
    console.log(`context-service listening on http://127.0.0.1:${port}`, JSON.stringify(engines.health()));
  });
}
