# AGENTS.md — Turbofan Maintenance Agent

Context for AI coding agents (Cursor) working in this repository. Read fully before making changes.

## 1. What we are building

A multi-agent assistant for a maintenance engineer. It answers: **"What is wrong with this engine, how long do I have, and what should I do?"** and returns an evidence-backed decision brief that the engineer approves before any work order is created.

- Event: GenAI.works **Open Agent Hackathon 2026**, solo entry, track **"The Agent That Can Explain Why"**.
- Build window: 22 Oct 00:00 UTC to **27 Oct 23:45 UTC (hard deadline)**. No new code after 27 Oct 12:00 UTC.
- Required sponsor tech: **Zetaris** and **Meterless** integrated; **Cursor** as IDE; **NVIDIA** where it contributes.
- Judging: Impact 30, Technical 20, Innovation 15, Demo 15, Product/UX 10, Sponsor tech 10. Judges reward working, focused, reliable systems with real agent interaction (critique, retries, branching, escalation) and clear traces; they penalise linear pipelines, hard-coded demo paths and bolted-on sponsor tech.
- Submission: 1–4 min demo video, slide deck, GitHub repo with README (setup, usage, dependencies), sponsor-tech explanation, logs/traces, sample inputs and outputs. Must run from a clean setup. `.env.example` only, no secrets.
- **Nightshift is the primary AI provider.** NVIDIA models go through Nightshift's Providers tab (the NVIDIA key is there now), not through an app `NVIDIA_API_KEY`. The critic calls Nightshift as its own app `turbofan-maintenance-critic` (`meterless-app` header), whose Judge lane is pinned to a Nemotron model; the trace records the served model and flags `same family as planner` when Claude or GPT answered.
- **`SAMPLE_MODE=true`** runs on committed Parquet snapshots with no Zetaris, Nightshift or network. Real logic, not replayed outputs. `run.py` is the CLI and `POST /run` entry point. Traces: `logs/trace-<run_id>.jsonl`.

Positioning: a **verifiable, open, portable maintenance decision layer** on top of existing systems (no data migration). Do not claim "first AI maintenance assistant" (Siemens Senseye/Industrial Copilot, Cognite Atlas AI RCA agents, IBM Maximo Assistant exist).

## 2. Design principles (non-negotiable)

1. **Advise and assure are separate.** Advisory agents propose; assurance gates can reject but never write the recommendation.
2. **Code computes, the LLM reasons.** Every number (RUL, trends, stock, lead times) comes from Python or SQL and is stored as an `Evidence` record. No LLM ever produces a number.
3. **Every claim cites evidence IDs.** A claim without a resolvable evidence ID fails the rule checks.
4. **Cheap checks first:** deterministic rule checks → LLM evidence critic → escalation rules → human sign-off.
5. **Disagreement is a signal.** Two independent RUL methods; divergence lowers confidence and triggers an inspection recommendation, never an average.
6. **Data stays where it is.** All structured data access goes through Zetaris (read-only). Snapshot fallback only when Zetaris is unreachable, and the brief says so. `SAMPLE_MODE` uses the snapshot only.
7. **Asset-specific knowledge is configuration** (asset pack manifest), never hard-coded in agents.
8. **Every run is traceable** via the case file trace.

## 3. Architecture

Layers (top to bottom):
1. **Streamlit UI**: ask, decision brief, trace, approve/reject.
2. **Agent layer (LangGraph, checkpointed)**: advisory agents + assurance gates.
3. **Context and tools**: case file + Meterless; Python model tools; document retrieval.
4. **Zetaris**: one governed SQL endpoint + semantic layer.
5. **Sources**: telemetry, maintenance (cmms), spares/slots, failure-mode table (S3), documents (pgvector).

### Agent graph (as of 23 Oct)
```
Intake → Planner → [Data | Prognostics | Diagnostics] (parallel)
→ Brief (claims) → Knowledge (documents, mandates) → Maintenance planner → Rule checks → Evidence critic
   ├─ revise → Maintenance planner (max 3 rounds)
   ├─ round 3 not accepted → close case: insufficient_evidence (gaps listed)
   └─ accept → Escalation rules → status needs_review (engineer sign-off is day 4 UI)
```

Knowledge retrieval, work-order write-back and H-MEM feedback are wired (23 Oct). Approve/reject stores the decision and gives each cited past case feedback (helpful on approve, not helpful on reject, unless the engineer marks it in the sign-off form; `wrong` = -0.20 trust and a review tag).

| Agent | Responsibility | Tools | Writes |
| --- | --- | --- | --- |
| Intake | Parse question, resolve asset, ask back | Deterministic parser + asset registry | `intent`, `asset` |
| Planner | Task list for round 1 specialists | none (deterministic) | `plan`, `round` |
| Data agent | Telemetry, history, schedule, hangar slots | Zetaris read-only SQL, snapshot fallback | `evidence` (query) |
| Prognostics | RUL A and B, SHAP drivers, anomaly flag | Model tools | `findings`, `evidence` (model) |
| Diagnostics | Fault mode; similar past cases | Signature matcher, H-MEM / SQLite | `findings.fault_modes`, `evidence` (case) |
| Knowledge | Applicable document sections in precedence order; mandates decided in code | `knowledge_pg.doc_chunks` via Zetaris (snapshot fallback), hashed n-gram embeddings | `findings.documents`, `evidence` (document) |
| Maintenance planner | Task, parts, slot, deadline from RUL lower bound | FMEA + stock + slots | `recommendation` |
| Evidence critic | Try to disprove; accept/revise/reject | Deterministic checks + Nightshift LLM when not sample mode | `verdicts` |

Rule checks and escalation rules are **deterministic graph nodes**, not agents. Intake does not call Scout Intent: Meterless World Model / H-MEM are used for similar cases, not for parsing.

## 4. Case file (shared state)

One per run; it is the LangGraph state; agents communicate only through it.

```python
class Evidence(BaseModel):
    id: str                      # "EV-DAT-01" — agent code + sequence, so parallel agents never collide
    kind: Literal["query", "model", "document", "case", "rule"]
    source: str                  # "zetaris:telemetry_pg.cycles", "snapshot:cycles.parquet", "model:rul_a@…"
    summary: str                 # one line the LLM and the brief read instead of raw rows
    content: dict
    produced_by: str
    degraded: str | None         # set when produced from a fallback, with the reason

class Claim(BaseModel):
    text: str
    evidence_ids: list[str]      # non-empty, resolvable
    confidence: Literal["low", "medium", "high"]

class CaseFile(BaseModel):
    case_id: str
    asset: AssetRef              # asset_id, asset_type, asset_pack version
    question: str
    intent: Intent
    plan: list[Task]
    evidence: dict[str, Evidence]
    findings: Findings           # rul_ml, rul_trend, fault_mode, drivers
    recommendation: Recommendation | None
    verdicts: list[Verdict]
    escalations: list[Escalation]
    round: int                   # max 3
    trace: list[TraceEvent]      # agent, input hash, output summary, verdict, retries, duration
    status: Literal["running", "needs_clarification", "needs_review", "approved", "rejected", "insufficient_evidence", "error"]
    degraded: list[str]
```

Evidence IDs match `^EV-[A-Z]{3}-\d{2,}$`. Trace events are also written to `logs/trace-<run_id>.jsonl` with the judge fields: timestamp, run_id, agent_name, action, input_summary, output_summary, target_agent, model, confidence, retry_count, status.

## 5. Assurance and reliability

**Rule checks (after every agent step, no LLM):** schema valid (re-ask once with the validation error, then fail step); every claim has resolvable evidence IDs; RUL within 0..125 cap and inside its interval; RUL and fault-mode numbers equal the model evidence they cite; asset ID matches; every part is listed in its cited evidence with the same stock and lead time; slot in the free list; deadline not later than the RUL lower bound allows; mandated document tasks present, cited and on time; SQL read-only and within row limits. The numbers-match and parts-match checks were added after the eval's seeded faults got past the first version.

**Evidence critic:** prompted to disprove. Deterministic checks always run (including sample mode): SHAP drivers must not be passed off as the fault-mode signature when the asset pack lists discriminating sensors. An LLM pass (`METERLESS_CRITIC_MODEL`, default `meterless/auto`, app `METERLESS_CRITIC_APP`, JSON response format, `CRITIC_TIMEOUT_S` 45 s) is added when Nightshift is up and `SAMPLE_MODE` is off. It runs only when the deterministic checks found no gap; on a fully cited brief its gaps are notes for the engineer, not a veto (it cannot produce numbers). Timeout, outage or a non-JSON reply → deterministic checks only, flagged `LLM critic unavailable (...)` in `degraded`. Returns `accept` | `revise` | `reject`. Max 3 rounds.

**Escalation rules (deterministic, thresholds from asset pack):**
| Condition | Action |
| --- | --- |
| RUL lower bound < parts lead time + days to next free slot | Urgent; propose expedite, engine swap, derate |
| RUL A vs B differ > 20 cycles (`rul_divergence_cycles`) | Confidence low; recommend borescope inspection |
| Two fault modes within 0.15 probability | Inspection targeting both modules |
| Applicable document mandates an action | Shown first; agents cannot remove it |

**Failure handling:** `tenacity` retries (3, exponential backoff); Zetaris down → Parquet snapshot + flag; Zetaris slow → 10 s per attempt (`ZETARIS_TIMEOUT_S`) and a 30 s live-query budget per run (`ZETARIS_BUDGET_S`), then snapshot + flag (Zetaris has random 15–170 s stalls on 1 s queries); Meterless down → SQLite `ContextStore`; hard limits 3 critic rounds, 25 graph steps, 120 s per run; LangGraph checkpointer resumes after crash.

**SQL guard:** parse with `sqlglot`; block everything except `SELECT`; add `LIMIT`; summarise large results in code, never pass raw rows to the LLM.

## 6. Data

| Source | Store | Content | Origin |
| --- | --- | --- | --- |
| Telemetry | Postgres db `telemetry`: `cycles` (wide), `signals`, view `telemetry_long` (canonical) | C-MAPSS FD001/FD003: 400 engines, 3 settings, 21 sensors | NASA (simulated) |
| Answer key | `telemetry.eval_ground_truth` | True RUL of the 200 active engines, proxy fault-mode labels | Eval only: SELECT revoked from `zetaris_ro` and `app_rw`; agents must never read it |
| Maintenance | Postgres db `cmms`: `assets`, `work_orders`, `findings`, `parts_replaced` | Inspections every 60 cycles, line checks, removals at failure | Generated from degradation curves; wording styled on FAA SDR |
| Spares and schedule | Postgres db `cmms`, tables prefixed `planning_` (`parts`, `stock`, `hangar_slots`, `flight_schedule`) | 20 part numbers, stock, lead times; hangar slots (2/day, 60 days); flight schedule (30 days) | Generated (`data/seed/parts.csv`) |
| Failure-mode table | S3 CSV/Parquet + Zetaris semantic layer | component → failure mode → sensor signature → task → part | Hand-written from Saxena C-MAPSS paper |
| Documents | Postgres db `knowledge` (pgvector), table `doc_chunks`; Zetaris `knowledge_pg.doc_chunks` | Synthetic manual rev 3 (+ superseded rev 2), 2 service bulletins, 1 airworthiness directive; one chunk per section | Synthetic (`asset_packs/.../docs`, `uv run task ingest-docs`) |

Canonical telemetry view: `asset_id, asset_type, cycle, ts, signal, value, unit, operating_regime`.

Fleet model (`data/fleet.py`): training units are historical engines `FD00x-H###` removed at failure; test units are the active fleet `FD00x-A###`, whose last observed cycle is the reference time 2026-10-22 00:00 UTC. 2 cycles per day. FD003 proxy fault mode: phi (`s_12`) rising over life means fan degradation, falling means HPC (P30, W31, W32 agree on all 100 training units). Generation is seeded and deterministic. Reload with `uv run task download`, `seed-telemetry`, `seed-cmms`, `register` (add `--target local` to the two seed tasks for the Docker database).
Label all generated data as synthetic in UI and README.

Other datasets: FAA SDR (faa.gov/av-info/download_SDR), Kaggle aircraft maintenance logs (merishnasuwal), Microsoft Azure Predictive Maintenance (second asset type, stretch), Federal Register API for ADs (roadmap only).

## 7. Models

| Function | Method |
| --- | --- |
| Preprocessing | Drop constant sensors; per-regime normalisation; rolling mean/slope 10/30 cycles |
| RUL A | LightGBM regression, target capped at 125; quantile models for 10th/90th interval |
| RUL B | Health index (linear combination of degrading sensors) + exponential extrapolation to threshold |

Status 22 Oct (`models/rul.py`, `uv run task train-rul`, artifacts and `rul_report.json` in `models/artifacts/turbofan_cmapss/`). Trained on 160 historical engines, 40 held out for early stopping; scored on the 200 active engines against the NASA RUL files (offline only):
- A: RMSE 12.1, MAE 8.6; 10th–90th interval covers 73% (nominal 80%). Features use the 14 sensors that move under any failure mode.
- B: RMSE 22.3, MAE 15.7; accurate near failure (RMSE ~7 when true RUL ≤ 25). Uses only the 9 sensors that move the same way under both failure modes, measured from each engine's own first 20 cycles (engines start with different wear). Returns the cap with a note when history < 20 cycles or the index is still inside the healthy fleet band (69 of 200 engines). Its range reflects threshold spread only and is narrow; show A's range in the brief.
- Disagreement rule changed from "> 25% or > 20 cycles" to "> 20 cycles": the relative rule flagged 44% of engines with no link to A's error; the absolute rule flags 27% and flagged engines have A error 11.6 vs 7.5.
- Inference input is the `cycles` rows of one asset from cycle 1 (B needs the full history; A needs the last 30 cycles). Values from Zetaris arrive as strings; `normalise` coerces them.
| Drivers | LightGBM TreeSHAP (`pred_contrib`) on A, summed per sensor then mapped to components via the asset pack. Negative values pull RUL down. No extra SHAP package. |
| Fault mode | Signature matcher on sensors whose direction differs across the variant's modes (`asset_packs/.../manifest.yaml` `variants`). Softmax temperature 0.0659, calibrated on historical FD003 engines cut at 30–100% of life (accuracy 0.965). FD001 has one mode in the pack so probability is 1.00. |
| Anomaly flag | Health index (B) minus the active-fleet median at the same cycle, in fleet standard deviations (`fleet_curve.json`, `anomaly_sigma: 2.0`). Flagged only when worse than the fleet. |
| Similar cases | H-MEM (Meterless) or SQLite keyword search, tagged by failure mode; skipped while the health index is still in the healthy band. |

## 8. Asset packs

All machine-specific knowledge lives in `asset_packs/<type>/manifest.yaml` (components, sensors with component/unit/degradation direction, `variants` listing failure modes per fleet dataset, failure_modes CSV, documents with type/revision/effective date, data_sources as Zetaris refs, model paths, thresholds). Agents read the pack; never hard-code turbofan specifics in agent code. Loaded by `data.db.load_pack`.

Knowledge agent precedence when sources conflict: airworthiness directive > OEM service bulletin > manual > internal procedure > past cases. Retrieve only chunks for the asset type, relevant components and the revision in effect.

## 9. Stack

Python 3.11+ with `uv`; LangGraph (+ InMemorySaver); Pydantic / pydantic-settings; LLMs via **Meterless Nightshift** (`tools/llm.py`, OpenAI-compatible `http://127.0.0.1:8088/v1`): planner model Claude Opus 4.7 (`meterless/auto`); critic `METERLESS_CRITIC_MODEL` (Nemotron 3 Super once NVIDIA enables the `edwinis` org — email help@build.nvidia.com). Do not put an NVIDIA key in the app. pandas, scikit-learn, LightGBM (TreeSHAP built in); Postgres + pgvector; S3; Meterless World Model + H-MEM vendored in `context-service/` (Apache 2.0, commit 1742dec) served by `node:http` on port **8710**; `tenacity`, `sqlglot`; Streamlit + Plotly (day 4); Docker Compose; pytest.

NVIDIA hosted NIM free tier ≈ 1,000 credits/month once the key exists: **cache model and tool results** in dev and eval. Hard limits: 3 critic rounds, 25 graph steps, 120 s per run (`RUN_TIMEOUT_S`).

## 10. Meterless integration

Node service in `context-service/` (`node:http`, `CONTEXT_SERVICE_PORT` default 8710): `GET /health`, `POST /world/load|/world/event|/world/query`, `GET /world/audit`, `POST /mem/write|/mem/search|/mem/feedback`, `GET /mem/ledger`.
- World Model: asset_type → component → sensor / failure_mode → task → part. Edges are single-valued; one-to-many (signatures, task parts) live on entity attrs.
- H-MEM: 200 synthetic past removals, source-keyed so reloads are idempotent. Diagnostics searches with `threshold=0` and failure-mode tags (default 0.35 dropped every case).
- Intake is a deterministic parser, not Scout Intent.
Python `ContextStore`: `MeterlessStore` (httpx) and `SqliteStore` (fallback). Default remains Meterless; the app falls back to SQLite when the service is down, and `SAMPLE_MODE` uses SQLite seeded from `data/snapshot/removals.parquet`. Day-2 decision: keep Meterless as default.

## 11. Infrastructure (AWS, region eu-central-1) — status as of 22 Oct, working

- **Zetaris Cloud** (Hobby tier) cluster **`edwinis-cluster`** on AWS EKS (`m7i-flex.large`), VPC `vpc-08f2c74862e4066e0`. Managed in *Zetaris Cloud Gateway*; data work happens in the cluster UI called **AI Data Gateway** (Data Explorer, Register Data Source, SQL Workspace, API Tokens, Unified Semantic Layer, Virtual Data Mart).
- **RDS PostgreSQL 17** instance `hackathon-pg` (endpoint in `.env.local` as `PG_HOST`), same VPC as EKS. Databases `telemetry`, `cmms`, `knowledge` (pgvector enabled), named in `PG_DB_TELEMETRY`, `PG_DB_CMMS`, `PG_DB_KNOWLEDGE`. SSL required (`sslmode=require`).
- DB users: `pgadmin` (master, Secrets Manager, auto-rotates; admin only), `loader` (seed/ingest, owns tables; default privileges grant SELECT to `zetaris_ro`, and SELECT/INSERT/UPDATE to `app_rw` in `cmms`), `zetaris_ro` (read-only, used only inside Zetaris), `app_rw` (app write path, `cmms` only).
- RDS security group: 5432 from the VPC CIDR (Zetaris EKS nodes) and the developer's IP.
- **S3** bucket `turbofan-hackathon-edwin` (private, SSE-S3), folders `fmea/`, `docs/`, `snapshots/`, `smoke/`. IAM user `zetaris-s3-reader` (GetObject/ListBucket on this bucket only) used by Zetaris. Uploads use the developer's own AWS profile.
- **Registered Zetaris data sources:** `telemetry_pg`, `cmms_pg`, `knowledge_pg` (JDBC Postgres), file source `s3_files` (S3 CSV). Cross-source join Postgres × S3 verified.
- Zetaris table addressing in SQL: `<datasource>.<table>`, e.g. `telemetry_pg.smoke`, `s3_files.smoke_s3`.
- Postgres `smoke` tables were dropped after seeding (22 Oct). `s3_files.smoke_s3` still exists.
- `s3_files.fmea` (16 rows) is registered from `s3://turbofan-hackathon-edwin/fmea/fmea.parquet`, built by `uv run task fmea` from `asset_packs/turbofan_cmapss/fmea.csv`. Join to `cmms_pg.planning_parts` / `planning_stock` on `part_number` verified (~2 s).
- S3 file tables are registered one file at a time in the Zetaris UI (no `REGISTER DATASOURCE TABLES`). The form's access key and secret are the IAM access key pair of `zetaris-s3-reader`, not a database login; a wrong key shows as `403 Forbidden` on `getFileStatus`.
- `loader` has no CREATE on the databases, so everything lives in schema `public` (the Zetaris sources point at `public`).
- `cmms_pg` was recreated on 22 Oct (stale `zetaris_ro` password). All 11 seeded tables are registered; `uv run task register` re-imports and counts them.
- Latency: a cross-source join (`cmms_pg.assets` × `telemetry_pg.cycles` × `cmms_pg.work_orders`) took ~13 s in Zetaris and ~45 s end to end. Filter by `asset_id` in SQL, keep queries narrow, and cache results to stay inside the 120 s run budget.

### Zetaris SQL dialect notes (learned)
- Create a JDBC source (one statement):
  `CREATE DATASOURCE <name> DESCRIBE BY "<desc>" OPTIONS (jdbcdriver "org.postgresql.Driver", jdbcurl "jdbc:postgresql://<host>:5432/<db>?sslmode=require", username "zetaris_ro", password "<pw>", schema "public");`
- `DESCRIBE BY` text may contain only letters, digits and `_ . - ,` (no spaces or parentheses).
- After creating new tables in a source: `REGISTER DATASOURCE TABLES FROM <name>;`
- The SQL Workspace runs only the current line: write each statement on one line (or select it before running). Programmatic access (REST/JDBC) sends whole statements.
- Engine is Spark SQL: quote odd identifiers with backticks.
- Never commit statements containing passwords.

### Data file conventions (learned)
- Column names `snake_case`, no spaces (a CSV header `id, note` produced a column named ` note`). Prefer Parquet over CSV on S3 (schema and types stored in file).

### Agent access to Zetaris
- Use the **REST API with a token** from *AI Data Gateway → API Tokens* (`ZETARIS_BASE_URL` = UI origin with no path, `ZETARIS_ORG_ID` = numeric ID, `ZETARIS_API_KEY` in `.env.local`; optional `ZETARIS_REST_URL` ending in `/api/v1.0`). These are the names the Zetaris getting-started scripts read.
- Verified 22 Oct: `POST {ZETARIS_BASE_URL}/api/proxy/sql-editor/sqls/run-query`, JSON body `{"sql", "limit"}`, headers `Authorization: Bearer <api key>`, `X-Org-ID`, `X-Request-ID` (UUID). Response keys: `data`, `headers`, `timeUsed`, `total`. Client: `tools/zetaris.py` (all SQL passes `assurance/sql_guard.py`).
- Developer machine sits behind a TLS-inspecting proxy: Python HTTP clients use `truststore` (Windows certificate store), and `uv` needs `UV_SYSTEM_CERTS=1`. Never disable certificate verification. JDBC is the fallback. Confirm REST endpoint shape from Zetaris docs (github.com/zetaris/getting-started, docs/connections).

## 12. Repository layout

```text
run.py             CLI and POST /run
agents/            intake, planner, data, prognostics, diagnostics, maint_planner, critic, rules, escalation
graph/             LangGraph graph, runner
assurance/         rule checks, escalation rules, sql guard
tools/             zetaris, data_access, data_tools, model_tools, llm
casefile/          schemas, trace, ContextStore, load-context
context-service/   Node service wrapping vendored Meterless engines
models/            train-rul, train-fault-mode, train-anomaly, artifacts/
evaluation/        run_eval (uv run task eval), seeded faults, report.md / report.json
asset_packs/turbofan_cmapss/   manifest.yaml, fmea.csv, docs/
data/snapshot/     committed Parquet extracts (no answer key)
input_examples/ output_examples/
docker-compose.yml  .env.example  README.md  AGENTS.md
```

## 13. Rules for coding agents

- **Secrets:** never read, print, copy or hard-code values from `.env.local` (or any `.env*` file). Reference env var names only. Never put example secrets in code, tests or docs. `.env*` files are git- and cursor-ignored; only `.env.example` is committed, and it lists every variable the code reads.
- **Cloud agents:** do not request security-group changes or access to AWS/Zetaris. Use `SAMPLE_MODE=true` (committed Parquet in `data/snapshot/`) and a local Docker Postgres for tests. Anything touching AWS runs locally on the developer's machine.
- **Agents are read-only** on data. Writes (work orders, findings, case memory) go through the application write service only.
- Every agent returns a typed Pydantic object; every numeric claim references an evidence ID.
- Keep functions small and testable; add a pytest for each rule check and escalation rule.
- Log every node to the case file trace.
- Prefer the simplest working version; do not add features outside the must/should list without asking.
- Declare any pre-existing components in the README.

## 14. Scope and plan

**Must:** sources in Zetaris; RUL A+B, SHAP, fault-mode matcher; full agent graph with critic loop, rule checks, escalation, retries, limits; Meterless (or fallback) shared state; trace + Streamlit brief + approve/reject with work-order write-back; 4 golden cases; video, slides, README, clean-setup test.
**Should:** document ingestion (1–2 PDFs) with cited retrieval; eval harness (RUL RMSE, evidence coverage, critic catch rate on seeded faulty briefs, run completion, fallback test); replay mode; findings form.
**Stretch:** second asset pack (Azure PdM) onboarded live.
**Cut:** separate mapping agent, curation UI, Zetaris branching, Federal Register live API, N-CMAPSS, separate risk agent.

Plan: D1 (22 Oct) done · D2 (23 Oct) done (models, Meterless engines, Nightshift client, schemas, data tools, end-to-end RUL) · D3 done (graph with critic loop, rule checks, escalation, maintenance planner) · D4 done early (documents, H-MEM feedback, UI) · D5 done early (eval harness `uv run task eval` → `evaluation/report.md`, 4 golden cases, end-to-end fallback tests, clean-setup test from a copy of the shippable files) · D6 README polish, video, slides, submit.

Eval (23 Oct, sample mode, 200 engines): 200/200 `needs_review`, median 0.3 s; RUL A RMSE 12.1, B 22.3; planning bound ≤ true RUL for 90% (1 of 45 engines within 30 cycles of failure overshoots, by 2 cycles); FD003 fault mode 97%; 100% of claims resolvable; 290/290 seeded faulty briefs caught by the deterministic gates, 0 false alarms. Known weakness: A's interval covers 73% (nominal 80%).

Golden demo cases: (1) healthy engine **FD001-A001**; (2) HPC degradation, parts in stock **FD001-A031**; (3) fan degradation **FD003-A100** (critic forces a second round: SHAP drivers are common-trend sensors); (4) methods disagree **FD001-A070** (A 96 vs B 40 cycles, HPC; escalation `rul_divergence`, borescope inspection, confidence low, deadline from the cautious bound). In disagreements B is usually the pessimistic one (offline check against the answer key), so inspecting rather than averaging is the safe call.

## 15. UI (decision brief)

Verdict line first (action, deadline, confidence, escalation in red); metric row (RUL A with range, RUL B, fault mode probability, similar cases); driving-sensor chart; evidence list with EV tags that open the underlying data; plan (task, parts, slot); one primary button "Approve and draft work order", plus Reject and comment; trace strip (critic rounds, escalations) with link to full trace. Supporting views: fleet overview, full trace, asset packs.

## 16. Open questions

- Nemotron critic lane: the NVIDIA key is in Nightshift and the critic app has a Judge lane. Nemotron 3.5 Lightning 30B A3B returned `502 provider_error ..._empty_response` even for "Reply with OK"; the Super VL 120B lane answered short prompts but timed out on the critic prompt. Next: try `nvidia/nemotron-3-super-120b-a12b` in the lane. Until it answers, live critic runs are deterministic-only and flagged.
- Do judges need a live Zetaris endpoint during judging (28 Oct–6 Nov)? `SAMPLE_MODE=true` must work regardless (it does).
