# AGENTS.md — Turbofan Maintenance Agent

Context for AI coding agents (Cursor) working in this repository. Read fully before making changes.

## 1. What we are building

A multi-agent assistant for a maintenance engineer. It answers: **"What is wrong with this engine, how long do I have, and what should I do?"** and returns an evidence-backed decision brief that the engineer approves before any work order is created.

- Event: GenAI.works **Open Agent Hackathon 2026**, solo entry, track **"The Agent That Can Explain Why"**.
- Build window: 22 Oct 00:00 UTC to **27 Oct 23:45 UTC (hard deadline)**. No new code after 27 Oct 12:00 UTC.
- Required sponsor tech: **Zetaris** and **Meterless** integrated; **Cursor** as IDE; **NVIDIA** where it contributes.
- Judging: Impact 30, Technical 20, Innovation 15, Demo 15, Product/UX 10, Sponsor tech 10. Judges reward working, focused, reliable systems with real agent interaction (critique, retries, branching, escalation) and clear traces; they penalise linear pipelines, hard-coded demo paths and bolted-on sponsor tech.
- Submission: 1–4 min demo video, slide deck, GitHub repo with README (setup, usage, dependencies), sponsor-tech explanation, logs/traces, sample inputs and outputs. Must run from a clean setup. `.env.example` only, no secrets.

Positioning: a **verifiable, open, portable maintenance decision layer** on top of existing systems (no data migration). Do not claim "first AI maintenance assistant" (Siemens Senseye/Industrial Copilot, Cognite Atlas AI RCA agents, IBM Maximo Assistant exist).

## 2. Design principles (non-negotiable)

1. **Advise and assure are separate.** Advisory agents propose; assurance gates can reject but never write the recommendation.
2. **Code computes, the LLM reasons.** Every number (RUL, trends, stock, lead times) comes from Python or SQL and is stored as an `Evidence` record. No LLM ever produces a number.
3. **Every claim cites evidence IDs.** A claim without a resolvable evidence ID fails the rule checks.
4. **Cheap checks first:** deterministic rule checks → LLM evidence critic → escalation rules → human sign-off.
5. **Disagreement is a signal.** Two independent RUL methods; divergence lowers confidence and triggers an inspection recommendation, never an average.
6. **Data stays where it is.** All structured data access goes through Zetaris (read-only). Snapshot fallback only when Zetaris is unreachable, and the brief says so.
7. **Asset-specific knowledge is configuration** (asset pack manifest), never hard-coded in agents.
8. **Every run is traceable** via the case file trace.

## 3. Architecture

Layers (top to bottom):
1. **Streamlit UI**: ask, decision brief, trace, approve/reject.
2. **Agent layer (LangGraph, checkpointed)**: advisory agents + assurance gates.
3. **Context and tools**: case file + Meterless; Python model tools; document retrieval.
4. **Zetaris**: one governed SQL endpoint + semantic layer.
5. **Sources**: telemetry, maintenance (cmms), spares/slots, failure-mode table (S3), documents (pgvector).

### Agent graph
```
Intake (Scout Intent) → Planner → [Data | Prognostics | Diagnostics | Knowledge] (parallel)
→ Maintenance planner → Rule checks → Evidence critic
   ├─ revise → back to Planner (max 3 rounds)
   ├─ round 3 not accepted → close case: insufficient_evidence (gaps listed)
   └─ accept → Escalation rules → Engineer sign-off → draft work order + case to H-MEM
```

| Agent | Responsibility | Tools | Writes |
| --- | --- | --- | --- |
| Intake | Parse question, resolve asset, detect ambiguity, ask back | Scout Intent, asset registry | `intent` |
| Planner | Build/revise task list, choose specialists, stop | World model, Markovian summary | `plan`, `round` |
| Data agent | Telemetry, maintenance history, spares, schedule | Zetaris read-only SQL, snapshot fallback | `evidence` (query) |
| Prognostics | RUL methods A and B, drivers, anomaly flags | Model tools | `findings`, `evidence` (model) |
| Diagnostics | Fault mode; similar past cases | Signature matcher, H-MEM | `findings.fault_mode`, `evidence` (case) |
| Knowledge | Applicable document passages | pgvector retrieval | `evidence` (document) |
| Maintenance planner | Task, parts, slot, deadline from RUL lower bound | Zetaris (spares, schedule), FMEA | `recommendation` |
| Evidence critic | Try to disprove; verdict accept/revise/reject with gaps | Read-only case file | `verdicts` |

Rule checks and escalation rules are **deterministic graph nodes**, not agents.

## 4. Case file (shared state)

One per run; it is the LangGraph state; agents communicate only through it.

```python
class Evidence(BaseModel):
    id: str                      # "EV-007"
    kind: Literal["query", "model", "document", "case", "rule"]
    source: str                  # "zetaris:telemetry", "manual.pdf#p12 rev C"
    content: dict
    produced_by: str

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
    status: Literal["running", "needs_review", "approved", "rejected", "insufficient_evidence"]
```

## 5. Assurance and reliability

**Rule checks (after every agent step, no LLM):** schema valid (re-ask once with the validation error, then fail step); every claim has resolvable evidence IDs; RUL within 0..125 cap; asset ID, units, cycle ranges match; parts and slots exist; SQL read-only and within row limits.

**Evidence critic:** different model family from the planner (NVIDIA Nemotron 3 Super). Prompted to disprove. Returns `accept` | `revise` (specific gaps) | `reject`. Max 3 rounds.

**Escalation rules (deterministic, thresholds from asset pack):**
| Condition | Action |
| --- | --- |
| RUL lower bound < parts lead time + days to next free slot | Urgent; propose expedite, engine swap, derate |
| RUL A vs B differ > 25% or > 20 cycles | Confidence low; recommend borescope inspection |
| Two fault modes within 0.15 probability | Inspection targeting both modules |
| Applicable document mandates an action | Shown first; agents cannot remove it |

**Failure handling:** `tenacity` retries (3, exponential backoff); Zetaris down → Parquet snapshot + flag; Meterless down → SQLite `ContextStore`; hard limits 3 critic rounds, 25 graph steps, 120 s per run; LangGraph checkpointer resumes after crash.

**SQL guard:** parse with `sqlglot`; block everything except `SELECT`; add `LIMIT`; summarise large results in code, never pass raw rows to the LLM.

## 6. Data

| Source | Store | Content | Origin |
| --- | --- | --- | --- |
| Telemetry | Postgres db `telemetry` | C-MAPSS FD001/FD003: unit, cycle, 3 settings, 21 sensors; test-set true RUL | NASA (simulated) |
| Maintenance | Postgres db `cmms` | Work orders, findings, parts replaced, notes | Generated from degradation curves; wording styled on FAA SDR |
| Spares and schedule | Postgres db `cmms` (own schema) | ~20 part numbers, stock, lead times; hangar slots (2/day); flight schedule | Generated |
| Failure-mode table | S3 CSV/Parquet + Zetaris semantic layer | component → failure mode → sensor signature → task → part | Hand-written from Saxena C-MAPSS paper |
| Documents | Postgres db `knowledge` (pgvector) | Synthetic manual + service bulletin, SDR extracts; chunk metadata | Synthetic + FAA SDR |

Canonical telemetry view: `asset_id, asset_type, cycle, ts, signal, value, unit, operating_regime`.
Label all generated data as synthetic in UI and README.

Other datasets: FAA SDR (faa.gov/av-info/download_SDR), Kaggle aircraft maintenance logs (merishnasuwal), Microsoft Azure Predictive Maintenance (second asset type, stretch), Federal Register API for ADs (roadmap only).

## 7. Models

| Function | Method |
| --- | --- |
| Preprocessing | Drop constant sensors; per-regime normalisation; rolling mean/slope 10/30 cycles |
| RUL A | LightGBM regression, target capped at 125; quantile models for 10th/90th interval |
| RUL B | Health index (linear combination of degrading sensors) + exponential extrapolation to threshold |
| Drivers | SHAP on A, aggregated to sensors → components via asset pack |
| Fault mode | Trend signature match vs FMEA; FD003 clustered into 2 groups → HPC / fan (proxy labels) |
| Anomaly flag | Residual vs fleet-average trajectory at same cycle |
| Similar cases | Nearest neighbours on signature in H-MEM, weighted by trust score |

## 8. Asset packs

All machine-specific knowledge lives in `asset_packs/<type>/manifest.yaml` (components, sensors with component/unit/degradation direction, failure_modes CSV, documents with type/revision/effective date, data_sources as Zetaris refs, model paths, thresholds). Agents read the pack; never hard-code turbofan specifics in agent code. Loaded via `make load-pack <type>`.

Knowledge agent precedence when sources conflict: airworthiness directive > OEM service bulletin > manual > internal procedure > past cases. Retrieve only chunks for the asset type, relevant components and the revision in effect.

## 9. Stack

Python 3.11+ with `uv`; LangGraph (+ checkpointer); Pydantic / pydantic-settings; LLMs: main model (Claude or GPT) for planner and tool-calling agents, **NVIDIA Nemotron 3 Super** for the critic, **Nemotron 3 Nano / 3.5 Lightning** for fast sub-agents (only if tool calling verified), **Nemotron Parse 2.0** for PDF parsing, **Nemotron 3 Embed** for embeddings (hosted API, OpenAI-compatible, base URL `https://integrate.api.nvidia.com/v1`; model IDs from build.nvidia.com, kept in `.env.local`); pandas, scikit-learn, LightGBM, SHAP; Postgres + pgvector; S3; Meterless reference engines (TypeScript) behind a Node/Fastify service; `tenacity`, `sqlglot`; Langfuse tracing; Streamlit + Plotly; Docker Compose; pytest; gitleaks.

NVIDIA free tier ≈ 1,000 credits/month (≈ 1 call each): **cache model and tool results** in dev and eval.

## 10. Meterless integration

Node service in `context-service/` exposing REST: `/world/query`, `/world/event`, `/mem/search`, `/mem/write`, `/intent/parse`.
- World Model: asset → module → sensor → failure mode → task → part; case events.
- H-MEM: past cases (signature, decision, outcome, comment, trust score).
- Scout Intent: intake. Markovian: bounded critic-loop summaries.
Python uses a `ContextStore` interface with two implementations: `MeterlessStore` and `SqliteStore` (fallback). **Decision point end of day 2**: if Meterless is not stable, switch default to SQLite.

## 11. Infrastructure (AWS, region eu-central-1) — status as of 22 Oct, working

- **Zetaris Cloud** (Hobby tier) cluster **`edwinis-cluster`** on AWS EKS (`m7i-flex.large`), VPC `vpc-08f2c74862e4066e0`. Managed in *Zetaris Cloud Gateway*; data work happens in the cluster UI called **AI Data Gateway** (Data Explorer, Register Data Source, SQL Workspace, API Tokens, Unified Semantic Layer, Virtual Data Mart).
- **RDS PostgreSQL 17** instance `hackathon-pg` (endpoint in `.env.local` as `PG_HOST`), same VPC as EKS. Databases `telemetry`, `cmms`, `knowledge` (pgvector enabled), named in `PG_DB_TELEMETRY`, `PG_DB_CMMS`, `PG_DB_KNOWLEDGE`. SSL required (`sslmode=require`).
- DB users: `pgadmin` (master, Secrets Manager, auto-rotates; admin only), `loader` (seed/ingest, owns tables; default privileges grant SELECT to `zetaris_ro`, and SELECT/INSERT/UPDATE to `app_rw` in `cmms`), `zetaris_ro` (read-only, used only inside Zetaris), `app_rw` (app write path, `cmms` only).
- RDS security group: 5432 from the VPC CIDR (Zetaris EKS nodes) and the developer's IP.
- **S3** bucket `turbofan-hackathon-edwin` (private, SSE-S3), folders `fmea/`, `docs/`, `snapshots/`, `smoke/`. IAM user `zetaris-s3-reader` (GetObject/ListBucket on this bucket only) used by Zetaris. Uploads use the developer's own AWS profile.
- **Registered Zetaris data sources:** `telemetry_pg`, `cmms_pg`, `knowledge_pg` (JDBC Postgres), file source `s3_files` (S3 CSV). Cross-source join Postgres × S3 verified.
- Zetaris table addressing in SQL: `<datasource>.<table>`, e.g. `telemetry_pg.smoke`, `s3_files.smoke_s3`.
- Smoke tables `smoke` (each DB) and `s3_files.smoke_s3` exist for testing; drop them after seeding.

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
agents/            intake, planner, data, prognostics, diagnostics, knowledge, maint_planner, critic
graph/             LangGraph graph, routing, checkpointer config
assurance/         rule checks, escalation rules, sql guard
tools/             zetaris client, model tools, retrieval
casefile/          Pydantic schemas, ContextStore interface + SQLite fallback
context-service/   Node service wrapping Meterless engines
models/            training scripts, saved models
asset_packs/turbofan_cmapss/   manifest.yaml, fmea.csv, docs/
data/              download + generation scripts (no raw data committed)
ui/                Streamlit app
eval/              harness, golden cases, results
docker-compose.yml  Makefile  .env.example  README.md  AGENTS.md
```

## 13. Rules for coding agents

- **Secrets:** never read, print, copy or hard-code values from `.env.local` (or any `.env*` file). Reference env var names only. Never put example secrets in code, tests or docs. `.env*` files are git- and cursor-ignored; only `.env.example` is committed, and it lists every variable the code reads.
- **Cloud agents:** do not request security-group changes or access to AWS/Zetaris. Use `ZETARIS_MODE=snapshot` (small Parquet samples in repo) and a local Docker Postgres for tests. Anything touching AWS runs locally on the developer's machine.
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

Plan: D1 (22 Oct) repo skeleton, compose, seed, sources in Zetaris · D2 models + Meterless or fallback (checkpoint: one question end to end returns RUL) · D3 full graph, rule checks, critic, escalation · D4 documents, H-MEM feedback, UI (feature freeze) · D5 eval, golden cases, fallback tests · D6 README, clean-setup test, video, slides, submit.

Golden demo cases: (1) healthy engine; (2) HPC degradation, parts in stock, approve; (3) fan degradation with RUL A/B disagreement → borescope; (4) critical engine, lead time > RUL → escalation, critic forces second round.

## 15. UI (decision brief)

Verdict line first (action, deadline, confidence, escalation in red); metric row (RUL A with range, RUL B, fault mode probability, similar cases); driving-sensor chart; evidence list with EV tags that open the underlying data; plan (task, parts, slot); one primary button "Approve and draft work order", plus Reject and comment; trace strip (critic rounds, escalations) with link to full trace. Supporting views: fleet overview, full trace, asset packs.

## 16. Open questions

- Does the Meterless agentathon starter wire H-MEM and World Model as a service?
- Main LLM choice for planner (Claude or GPT).
- Do judges need a live Zetaris endpoint during judging (28 Oct–6 Nov)? Snapshot mode must work regardless.
