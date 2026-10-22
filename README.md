# MaintenanceAssistant

Evidence-backed maintenance decision agent for turbofan engines. Open Agent Hackathon 2026, track "The Agent That Can Explain Why". See `AGENTS.md` for the design.

All maintenance, spares and document data in this project is synthetic. Telemetry is the NASA C-MAPSS simulated dataset.

## Prerequisites

- Python 3.11 and [uv](https://docs.astral.sh/uv/)
- Node.js 24 LTS (Meterless context service)
- Docker Desktop (local Postgres for tests)

## Setup

```powershell
uv sync
Copy-Item .env.example .env.local   # then fill in values; never commit .env.local
```

`ZETARIS_MODE=snapshot` runs without any cloud access. Set `ZETARIS_MODE=live` to query Zetaris.

## Commands

| Command | What it does |
| --- | --- |
| `uv run task test` | Unit tests |
| `uv run task check` | Read-only connectivity check against RDS and Zetaris (live credentials required) |
| `uv run task db-up` | Local Postgres 17 with pgvector on `127.0.0.1:5433`, databases `telemetry`, `cmms`, `knowledge` |
| `uv run task db-down` | Stop local Postgres |
