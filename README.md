# MaintenanceAssistant

Evidence-backed maintenance decision agent for turbofan engines. Open Agent Hackathon 2026, track "The Agent That Can Explain Why". Design notes: `AGENTS.md`.

It answers: **what is wrong with this engine, how long do I have, and what should I do?** Every number is computed in Python or SQL and stored as evidence. Claims without a resolvable evidence ID fail the rule checks.

All maintenance, spares and document data in this project is **synthetic**. Telemetry is the NASA C-MAPSS simulated dataset (FD001 / FD003).

## Prerequisites

- Python 3.11 and [uv](https://docs.astral.sh/uv/)
- Node.js 24 LTS (Meterless context service; not needed for `SAMPLE_MODE`)
- Nightshift desktop app (Meterless runtime on port 8088; not needed for `SAMPLE_MODE`)
- Docker Desktop (optional; local Postgres for tests)

## Setup

```powershell
uv sync
Copy-Item .env.example .env.local   # then fill in values; never commit .env.local
```

## Sample mode (no credentials, no network)

```powershell
$env:SAMPLE_MODE = "true"
uv run python run.py --question "How long can FD003-A100 keep flying and what is wrong with it?"
```

This uses the committed Parquet extracts in `data/snapshot/` and a local SQLite case memory. It does not call Zetaris, Nightshift or the internet. Sample inputs and outputs: `input_examples/`, `output_examples/`.

`uv run python run.py --serve` exposes `POST /run` with JSON `{"question": "..."}` and structured errors.

## Live mode

1. Start Nightshift. Put the Console runtime token in `METERLESS_RUNTIME_TOKEN`. Add provider keys in Nightshift **Setup → Providers**, not in this repo. The NVIDIA key belongs there once NVIDIA enables the account.
2. Start the context service: `uv run task context-service` (port 8710). Load once with `uv run task load-context` (idempotent).
3. Set `SAMPLE_MODE=false` and `ZETARIS_MODE=live` in `.env.local`.
4. `uv run python run.py --question "..."` — Zetaris is queried first; on failure the run switches to the snapshot and every later evidence record is flagged `degraded`.

`uv run task check-llm` confirms Nightshift. `uv run task check` confirms RDS and Zetaris.

## Commands

| Command | What it does |
| --- | --- |
| `uv run task test` | Unit tests |
| `uv run python run.py --question "..."` | One decision brief as JSON |
| `uv run python run.py --serve` | `POST /run` on `RUN_HOST:RUN_PORT` |
| `uv run task check` | Read-only connectivity check (live credentials) |
| `uv run task check-llm` | Nightshift `GET /v1/models` plus one chat request |
| `uv run task context-service` | Meterless World Model + H-MEM on 127.0.0.1:8710 |
| `uv run task load-context` | Load asset pack + 200 past cases into the context store |
| `uv run task snapshot` | Rebuild `data/snapshot/*.parquet` (`--verify` compares with Zetaris) |
| `uv run task train-rul` | Remaining-life methods A (LightGBM) and B (health-index trend) |
| `uv run task train-fault-mode` | Calibrate the signature matcher |
| `uv run task train-anomaly` | Fleet health-index curve for the anomaly flag |
| `uv run task download` | NASA C-MAPSS FD001/FD003 into `data/raw/` (not committed) |

## Sponsor tech

- **Zetaris:** all structured reads go through the AI Data Gateway REST SQL API (`tools/zetaris.py`), guarded to `SELECT` only. Snapshot fallback when it is down.
- **Meterless:** (1) Nightshift is the primary model provider (`tools/llm.py`). (2) World Model and H-MEM run in `context-service/`, wrapping the official reference engines.
- **NVIDIA:** Nemotron is the intended critic model, reached through Nightshift, not a direct app key. Pending NVIDIA account verification for org `edwinis`.
- **Cursor:** this repository was developed in Cursor.

## Pre-existing components

- NASA C-MAPSS FD001/FD003 (simulated turbofan run-to-failure). Not committed; `uv run task download`.
- Meterless H-MEM and World Model reference engines, vendored at commit `1742dec` under Apache License 2.0. See `context-service/vendor/VENDORED.md`.
- Failure physics follow Saxena, Goebel, Simon, Eklund (2008). The FMEA table and all CMMS / planning data are generated and labelled synthetic.

## Traces

Each run writes `logs/trace-<run_id>.jsonl` (timestamp, run_id, agent_name, action, input_summary, output_summary, target_agent, model, confidence, retry_count, status). The served model name is logged for every LLM request.
