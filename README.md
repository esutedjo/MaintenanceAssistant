# Cleris

The MRO clearance assistant. An evidence-backed maintenance decision agent for turbofan engines. Open Agent Hackathon 2026, track "The Agent That Can Explain Why". Design notes: `AGENTS.md`.

**Demo video (3:52, 1440p):** https://youtu.be/rbfwq12gzoI

**Replay of six live runs, nothing to install:** https://esutedjo.github.io/MaintenanceAssistant/ (each brief with its evidence, gates, decision log and every Zetaris, World Model, H-MEM and Nemotron call)

It answers: **what is wrong with this engine, how long do I have, and what should I do?** Every number is computed in Python or SQL and stored as evidence. Claims without a resolvable evidence ID fail the rule checks.

**Why it matters, in practitioners' words** (short excerpts, verified at source on 26 Oct 2026):

- Japan Airlines' VP of IT and digital promotion, Sugimoto Kei: "a big challenge for us is really our data and it's very disparate." ([Constellation Research](https://constellationr.com/insights/news/how-aviation-maintenance-looks-ai-systems), Oct 2026)
- Sarunas Kisevicius, EngineStands.com: "spare engine pools have become much thinner" as engines spend longer in overhaul. ([Aviation Week](https://aviationweek.com/mro/supply-chain/tightening-parts-supply-intensifies-airlines-summer-aog-pressures), 14 Jul 2026)
- Alice Belcher, Delta TechOps, on predictive alerts: "The analytic itself does not make maintenance decisions." Engineers investigate and decide. ([Aviation Week](https://aviationweek.com/mro/emerging-technologies/false-alerts-test-trust-predictive-maintenance-systems), 19 Oct 2026)

All maintenance, spares and document data in this project is **synthetic**. Telemetry is the NASA C-MAPSS simulated dataset (FD001 / FD003).

> **Hackathon prototype.** Runs on NASA C-MAPSS simulated telemetry and synthetic maintenance, spares and document data. Not certified or approved by any aviation authority and not for operational use. It does not issue a return to service or a certificate of release to service: approval drafts a work order for the organisation's own release process.
>
> **Intended use:** advisory decision support for the engineer. Never the sole source of an airworthiness determination or a change to maintenance intervals. "Clearance" in the product line means the engineer's decision to keep, inspect or remove an engine; the release to service stays with certifying staff in the organisation's own system. See [`docs/regulatory-requirements.md`](docs/regulatory-requirements.md).

**Judges / clean clone:** set `SAMPLE_MODE=true` and run. That path uses only committed Parquet snapshots and SQLite case memory. It does not need Zetaris, Nightshift, Docker, Node, or any credentials. If a live service is down during judging, the same snapshot path is used automatically and the brief is flagged `degraded`. To see what the live path did (Zetaris on RDS and S3, the Meterless services, Nemotron through Nightshift) without credentials, open the [replay page](https://esutedjo.github.io/MaintenanceAssistant/) or run `uv run task live-evidence` ([Check the live path without credentials](#check-the-live-path-without-credentials)).

```powershell
uv sync
$env:SAMPLE_MODE = "true"
uv run python run.py --question "What should I do about FD001-A031?"
# or: docker build -t cleris . ; docker run --rm -p 127.0.0.1:8000:8000 cleris
# then: curl http://127.0.0.1:8000/health
#       curl -X POST http://127.0.0.1:8000/run -H "Content-Type: application/json" --data-binary @input_examples/critical_hpc.json
```

macOS / Linux:

```bash
# LightGBM needs OpenMP: macOS `brew install libomp`; minimal Debian/Ubuntu `sudo apt install libgomp1`
uv sync
SAMPLE_MODE=true uv run python run.py --question "What should I do about FD001-A031?"
SAMPLE_MODE=true uv run task ui        # decision brief UI on http://localhost:8501
```

No `.env.local` or other credentials are needed: `.env*` files are git-ignored (only `.env.example` with placeholders is committed) and excluded from the Docker image. Checked on 26 Oct 2026 from a fresh clone with no credentials: CLI, `POST /run`, the test suite, `docker build` / `docker run` and the UI all ran in sample mode on Windows 11 and on Ubuntu 24.04 (with `libgomp1`). macOS was not tested natively; the Docker image is multi-architecture (amd64 and arm64). Without `SAMPLE_MODE`, a run still completes on the snapshot and the brief is flagged `degraded` ("LLM critic unavailable").

## Prerequisites

- Python 3.11 and [uv](https://docs.astral.sh/uv/)
- The OpenMP runtime that LightGBM links against. Windows and most Linux desktops already have it. On macOS run `brew install libomp`; on a minimal Debian or Ubuntu, `sudo apt install libgomp1`. The Docker image includes it. If it is missing, the run stops with these instructions.
- Node.js 24 LTS (Meterless context service; not needed for `SAMPLE_MODE`)
- Nightshift desktop app (Meterless runtime on port 8088; not needed for `SAMPLE_MODE`)
- Zetaris (live mode only): a cluster with the three Postgres databases (`telemetry_pg`, `cmms_pg`, `knowledge_pg`) and the S3 bucket (`s3_files`) registered as sources; see `uv run task register`. Not needed for `SAMPLE_MODE`.
- Docker Desktop (optional; local Postgres for tests)

## Setup

```powershell
uv sync
```

Sample mode needs no environment file. For live mode, copy `.env.example` to `.env.local` and replace the empty placeholders. `.env.example` has names only; `.env.local` is gitignored. The names are the fields of `Settings` in `settings.py`. The NVIDIA key stays in Nightshift's Providers tab.

## Sample mode (no credentials, no network)

```powershell
$env:SAMPLE_MODE = "true"
uv run python run.py --question "How long can FD003-A100 keep flying and what is wrong with it?"
```

This uses the committed Parquet extracts in `data/snapshot/` and a local SQLite case memory. It does not call Zetaris, Nightshift or the internet. Sample inputs and outputs: `input_examples/`, `output_examples/`.

A question that names no engine ID goes to Nemotron in live mode. Sample mode replays one recorded reply for it, for this exact question: `uv run python run.py --question "Should we pull the engine with serial 700131?"` resolves to FD001-A031, and the intake is labelled "recorded Nemotron response". Other wording without an engine ID gets a question back asking which engine.

`uv run python run.py --serve` exposes `POST /run` with JSON `{"question": "..."}` (optionally `"inspections": [...]`) and structured JSON errors; requests are capped at 64 KB and four concurrent runs (JSON 503 when busy). Storing inspection findings through the API (`"record": true`) is refused unless the server sets `API_ALLOW_RECORD=true`. `GET /health` reports each component (mode, Zetaris and its RDS and S3 sources, the Meterless context service, Nightshift, the CMMS write path) as active, degraded or not used, without hosts or credentials. Sample mode uses SQLite case memory unless `CONTEXT_STORE=meterless` is set explicitly and the local context service answers.

```powershell
$env:SAMPLE_MODE = "true"
uv run task ui
```

Opens the decision brief at http://127.0.0.1:8501 (laid out for desktop and tablet). Approve drafts a work order to `.state/draft_work_orders.jsonl` in sample mode; live mode with `PG_APP_USER` inserts into `cmms.work_orders`.

What a reviewer can check in the UI, without reading code:

- **Services strip**: chips under the ask bar show whether Zetaris (with its RDS Postgres and S3 sources), the Meterless context service (World Model, H-MEM), Nightshift (and the Nemotron model it served) and the CMMS write path are active, green, amber or grey, with latency and the last successful query; "Services" opens the details. Checks are read-only, time out at 2.5 s, run in the background and are reused for 45 s. In sample mode the strip says "Sample mode: snapshot · SQLite · recorded Nemotron" and makes no network call.
- **How the brief was checked**: chips under the verdict give the rule-check result, critic rounds and the model that answered. The Trace tab shows every step as a timeline.
- **Why this plan and not another**: each rule the planner applied (severity band, swap against parts lead time plus the first free slot, method disagreement, mandates), this engine's value, and the value at which the plan would change. Computed from the planner's decision record and the asset pack thresholds.
- **Stress test**: inject any of the 19 seeded mistakes (tampered remaining life, fabricated part, dropped mandate, and others) into the brief on screen and see which gate stops it. The same faults and gates as the evaluation.
- **Fleet triage**: "Which engines need attention this week?" is answered from `data/snapshot/fleet_triage.parquet`, which `uv run task fleet-triage` builds by running the full agent graph on all 200 active engines (sample mode, about 75 s). The Fleet tab shows when it was computed and can recompute it.

## Agents and how they coordinate

Not a fixed pipeline: the planner delegates follow-ups when the findings call for them, and every gap a gate finds goes to the agent that can fix it.

```mermaid
flowchart LR
  Q[Question] --> I[Intake]
  I --> P[Planner]
  P --> D[Data] & PR[Prognostics] & DG[Diagnostics]
  D & PR & DG --> B[Brief]
  B --> RV{Planner review}
  RV -- "methods disagree, fault unclear, bad input" --> FU[Follow-ups: data, prognostics, diagnostics]
  RV -- clear case --> K[Knowledge]
  FU --> K
  K --> M[Maintenance planner]
  M --> RC{Rule checks}
  RC -- pass --> C{Critic: code checks + Nemotron}
  RC -- gap --> R[Planner routes each gap]
  C -- gap --> R
  R -- "mandate" --> K2[Knowledge]
  R -- "part, stock, slot" --> D2[Data]
  R -- "no similar cases" --> DG2[Diagnostics]
  R -- "RUL mismatch" --> PR2[Prognostics]
  R -- "number, deadline, signature" --> M
  K2 & D2 & DG2 & PR2 --> M
  R -- "no agent can fix, or round 3" --> X[Close: insufficient evidence]
  C -- accept --> E[Escalation rules] --> S[Engineer sign-off: decision log, draft work order]
```

| Agent or gate | Does | Uses a model? | Can block or send back? |
| --- | --- | --- | --- |
| Intake | Resolves the engine; parser first | Nemotron only for free text; code checks the quote and the registry | Asks back when no engine is found |
| Planner | Assigns the specialists, reviews their findings and delegates follow-ups, routes each gap to its owner | No | Routes; closes a case no agent can fix |
| Data | Telemetry, history, stock, slots through Zetaris; input plausibility gate | No | Raises a data-quality escalation |
| Prognostics | RUL A (LightGBM quantiles, calibrated) and RUL B (health-index trend), SHAP drivers, back-test at the last inspection | No | No |
| Diagnostics | Fault mode, discriminating sensors, similar past cases from H-MEM | No | No |
| Knowledge | Documents in effect, in precedence order, grouped mandatory, recommended or reference; mandates decided in code; the fleet's engineering orders | No | Mandate escalation |
| Maintenance planner | Task from the World Model, parts, slot, deadline, method of compliance, work package, every claim with evidence | No | No |
| Rule checks | Deterministic checks on every claim and number | No | Sends back |
| Critic | Deterministic checks, then Nemotron tries to disprove the brief; code verifies each challenge | Nemotron (live; recorded replies in sample mode) | Sends back only on a challenge code confirms |
| Escalation rules | Fixed rules from the asset pack | No | Escalates |
| Engineer | Approves or rejects with name, acknowledgements and a reason for urgent escalations | Human | Decides |

**Follow-ups.** After round 1 the planner checks the findings. If the two remaining-life methods disagree beyond the pack's limit, the Data agent pulls the inspection history and prognostics back-tests both methods at the last inspection. If the fault mode is unclear, diagnostics reads the discriminating sensors and past cases for both candidates. If the input failed a plausibility check, the Data agent re-reads it. Follow-ups only make the plan more cautious, and a clear case logs "no follow-up needed" with the reason. Routing table: `assurance/routing.py`.

**Nemotron in sample mode.** Judges usually run without Nightshift, so sample mode replays Nemotron replies captured from live calls on 26 Oct 2026 (`data/snapshot/llm_recordings.json`, model `nvidia/nemotron-3-ultra-550b-a55b`). A reply is used only when the prompt hash matches exactly; code verifies every replayed challenge as in live mode, and the trace, decision log and brief label it "recorded Nemotron response". A changed prompt falls back to the deterministic checks, and a test fails if the golden prompts drift (`uv run task record-llm` re-captures).

**Decision log and sign-off.** Every agent, gate, model and human decision is recorded in order (Details, Decision log tab; `casefile/decision_log.py`). To approve, the engineer types a name (role and licence reference optional), ticks each escalation as read and gives a reason for urgent ones; the entry binds to the brief by a SHA-256 of the recommendation. The log is stored append-only with a SHA-256 hash chain under `.state/decision_logs/` and attached to the draft work order, which says it is not a certificate of release to service.

**Inspection findings close the loop.** When the brief asks for a borescope, the result comes back in: the engineer (UI "Add inspection finding") or the API (`"inspections"` with the question) enters borescope, visual, oil-analysis, chip-detector or material-test findings. Each becomes evidence (`EV-INS-nn`, "entered by <inspector>"); Knowledge cites the manual's limit table (synthetic `AMM-TF-R3 …-601` sections, revision in effect only); code classifies the finding as within, at or beyond the limit; diagnostics says whether it corroborates or weakens the fault mode; and the planner acts: beyond the limit, remove before further flight (urgent escalation and a "potentially reportable" note); at the limit, re-inspect within the manual's interval; within the limit on every suspected module, no removal. A finding never relaxes a plan driven by remaining life or a mandate, and a rule check (`inspections_acted_on`) plus a seeded fault (`inspection_ignored`, 30 of 30 caught) keep it that way. Example inputs: `input_examples/inspection_within_limits.json` and `inspection_beyond_limits.json` (FD001-A070); the UI has "Load example finding" buttons, labelled as example data.

**What an engine engineer asked for (26 Oct).** A certified-engineer review of the brief led to five additions. All of them use the same evidence rules as the rest of the brief:

- **Documents by what they mean for compliance.** Mandatory now (a mandate whose conditions hold), Recommended (a service bulletin, which needs engineering approval to adopt) and Reference (manuals, procedures, a declined bulletin, and a mandate whose conditions are not met, with the reason, for example "RUL lower bound 115 ≥ 30 cycles").
- **Method of compliance.** Each mandatory document gets a compliance line on the plan and the draft work order, for example "AD-2026-17-04 (g): complied with by engine removal before the 10-cycle limit (removal at the first free slot 2026-10-23, limit 2026-10-27); FM-HPC-01 (borescope inspection HPC stages 1-9) at shop induction; record in the AD compliance record". A rule check sends back a plan that has no line, or a line that ends after the limit. Two seeded faults test this check (`compliance_dropped`, `compliance_late`).
- **Work package.** Asset-pack templates (`work_packages.yaml`) turn the plan into tasks with synthetic task references, required inspection items (RII), tooling, manhours and skill. A lease-engine swap adds the items to verify before installation, which Cleris has no data on: configuration and SB/AD status, release certificate, LLP cycles for the lease term.
- **What the shop found.** Similar past removals now say what the shop found and replaced (from the CMMS record), not only that the engine was removed.
- **Engineering orders.** A recommended bulletin is decided once per fleet, not on every brief. In the UI, the sb_adoption note has "Record fleet decision": adopt or decline, with a name. The decision is stored with a SHA-256 hash chain in `.state/engineering_orders.jsonl`. Later briefs for that fleet cite it, and the note stops. Until then, the plan follows the manual section that covers the task.

**Help for non-experts.** A Help button and "What is this?" links open one panel: what each part of the screen means for the open engine, a searchable glossary of 105 terms and abbreviations plus the asset pack's 21 sensors, an engine diagram with the sensors and fault modes, credited photos, and "Ask Cleris": Nemotron explains the brief from the fields on screen, and code flags any number in its answer that the brief does not contain.

## Documents (Knowledge agent)

`asset_packs/turbofan_cmapss/docs/` holds five SYNTHETIC documents: maintenance manual revision 3, a superseded revision 2, two service bulletins and an airworthiness directive. Each section is one chunk with metadata (document type, revision, effective date, fleet variant, components, failure modes, FMEA tasks, optional topic and mandate).

The Knowledge agent runs after the specialists, because what applies depends on their fault mode and remaining-life lower bound. It:

1. reads the chunks for the asset type (Zetaris `knowledge_pg.doc_chunks` live, `doc_chunks.parquet` in sample mode);
2. keeps revisions in effect on the reference date, for the fleet variant and the suspected components;
3. ranks them by cosine similarity to a query built from the findings, keeping the top 4;
4. orders them by precedence (airworthiness directive > service bulletin > manual > procedure) and marks a lower document overridden when a higher one covers the same topic;
5. decides in code whether a mandate's conditions hold (fault-mode probability, RUL lower bound).

A mandate that applies is listed first among the escalations. The maintenance planner must include its FMEA task and deadline, and a rule check sends the plan back if the task is missing, uncited or deferred. Embeddings are hashed word uni- and bigrams (scikit-learn `HashingVectorizer`, 512 dimensions), so sample mode retrieves exactly like live mode with no model download.

## Golden cases

| Engine | Question in the UI | What should happen |
| --- | --- | --- |
| FD001-A001 | Healthy | RUL at the cap, no wear; no action proposed before the next scheduled inspection, no similar cases searched |
| FD001-A031 | HPC swap | HPC degradation, RUL lower bound shorter than short-part lead time plus next free slot: lease-engine swap, airworthiness directive task kept in the plan |
| FD003-A100 | Fan swap | Fan degradation; the critic sends round 1 back because the SHAP drivers are common-trend sensors, round 2 names the distinguishing sensors |
| FD001-A070 | Methods disagree | RUL A 96 vs B 40 cycles: escalation `rul_divergence`, borescope inspection, confidence low, deadline from the cautious bound |

Inputs and outputs: `input_examples/*.json`, `output_examples/*.json`.

## Assurance

Advisory agents propose; the gates can only send work back or stop it.

0. **Input plausibility** (`tools/data_quality.py`, run by the Data agent before any model output is used, no LLM): telemetry outside the range seen on any C-MAPSS engine (plus a margin), jumps between cycles larger than any engine showed, a model sensor frozen for 30 cycles, missing values, duplicate, backward or missing cycles, telemetry ending at a different cycle from the asset registry, future timestamps, work orders after the last cycle, a removal recorded on an active engine, negative stock or lead time. Limits are in the asset pack (`data_quality` in `manifest.yaml`, ranges in `sensor_ranges.json` built by `uv run task sensor-ranges`). A serious issue (anything a model reads, or the stock) sets confidence low, puts "Confirm the input data first" in front of the action and raises a `data_quality` escalation; a minor one (a sensor the models ignore, a gap, a stray date) is flagged in the brief. Impossible stock counts as none available.
1. **Rule checks** (`assurance/rules.py`, no LLM): every claim cites evidence IDs that resolve; RUL values in 0..125 with the estimate inside its interval; RUL and fault-mode numbers equal the model output they cite; asset matches the question; every part is listed in the evidence it cites, with the same stock and lead time; the slot is in the free-slot list; the deadline is not later than the RUL lower bound allows; mandated document tasks are present, cited and on time, each with a method of compliance that completes within the document's limit; every task is on the World Model's list for the fault; every number a claim states is in its cited evidence (or a pack threshold, a sum or difference of two headline values, or a cycles-to-days conversion). On the 200-engine fleet, 0 of 3,401 clean claims fail the number check.
2. **Evidence critic** (`agents/critic.py`): deterministic checks always run (SHAP drivers must not be passed off as the fault-mode signature; a degraded engine needs similar past cases). When Nightshift is up and `SAMPLE_MODE` is off, Nemotron tries to disprove the brief; in sample mode the critic replays a recorded Nemotron reply when the prompt matches one exactly, labelled "recorded", and code checks it the same way. Its free-text gaps are shown to the engineer. Its checkable challenges (`assurance/challenges.py`: unsupported number, missing mandate, part not in evidence, deadline after the bound, wrong engine) are verified by code: a confirmed challenge sends the brief back, a refuted one is shown as refuted. It never produces a number. Max 3 rounds; round 3 without acceptance closes the case as `insufficient_evidence`.
3. **Escalation rules** (`assurance/escalation.py`, thresholds from the asset pack): input data failed a serious plausibility check; RUL lower bound shorter than short-part lead time plus next free slot; RUL A vs B differ by more than 20 cycles; two fault modes within 0.15; an applicable document mandates an action; an inspection finding is beyond or at the manual limit; the live FMEA lists tasks the World Model does not link to the fault (`knowledge_drift`); a recommended service bulletin covers a planned task and engineering has not decided on it for the fleet (`sb_adoption`, info: engineering decides once per fleet with an engineering order; the plan follows the manual meanwhile); and a note when a condition may be reportable under the operator's occurrence-reporting scheme (`potentially_reportable`, info: the decision to report stays with people). Urgent escalations and mandatory documents must be ticked as read before Approve, urgent ones with a reason.
4. **Engineer sign-off** in the UI. Nothing is written before Approve.

The LLM critic runs as its own Nightshift app (`METERLESS_CRITIC_APP=turbofan-maintenance-critic`, sent as the `meterless-app` header) so Nightshift can pin it to a Nemotron lane whatever `meterless/auto` would pick for other apps. In Nightshift, give that app a Judge lane with an NVIDIA Nemotron model. The trace records the model that actually answered and notes `critic lane not pinned to Nemotron` when Claude or GPT answered.

## Reliability and fallbacks

| Failure | What the run does | What the brief says |
| --- | --- | --- |
| Zetaris down or not configured | Snapshot for the rest of the run | `Zetaris unavailable (...)` / `Zetaris not configured`; every later evidence record flagged `degraded` |
| Zetaris slow | Each query times out after `ZETARIS_TIMEOUT_S` (10 s, 3 attempts); after `ZETARIS_BUDGET_S` (30 s) of live query time the run switches to the snapshot | `Zetaris slow (N s of the 30 s query budget used)` |
| Context service (Meterless) down | SQLite case memory | `context-service unreachable ...; using SQLite` |
| Nightshift down, critic timeout (`CRITIC_TIMEOUT_S`, 45 s) or a non-JSON critic reply | Deterministic critic checks only; the run still completes | `LLM critic unavailable (...); deterministic critic checks only` |

Hard limits: 3 critic rounds, 25 graph steps, 120 s per run. If the LLM critic would exceed the remaining run time, it is skipped and the deterministic checks still finish the brief. `tests/test_fallbacks.py` runs each failure end to end offline.

## Case memory feedback

When the engineer approves or rejects, the decision is stored in case memory (H-MEM, or SQLite when the context service is down). Every similar past case the brief cited also gets feedback: helpful on approve and not helpful on reject, unless the engineer marks it helpful, not helpful or wrong in the sign-off form. Trust moves by +0.05, -0.03 or -0.20 (wrong also tags the case for review), and the next similar-case search ranks by relevance times trust. The brief shows each case's trust before and after.

## Evaluation

```powershell
uv run task download   # once: NASA answer key into data/raw/ (eval only; agents never read it)
uv run task eval       # 200 active engines in sample mode, about 1 minute
```

Writes `evaluation/report.md` and `report.json`. Results from the committed snapshot:

- **Decisions against what happened:** of the 45 engines that fail within 15 days (NASA answer key), 45 were sent for removal, swap or repair and none was told to keep flying. Of the 102 with more than 40 days left, 60 keep flying, 41 get an inspection and 1 is pulled early. Of the 53 in between, 1 is told to keep flying.
- **Completion:** 200 of 200 runs reach `needs_review`, median 0.3 s per run. Critic rounds: 163 runs need one, 37 a second.
- **Coordination:** planner follow-ups ran on 54 of 200 runs (inspection history and a back-test where the two methods disagree) and brought the inspection forward on 3; gaps routed to the agent that fixes them: 37 (all sensor-signature gaps, to the maintenance planner).
- **Remaining life:** RUL A RMSE 12.1; its 10th–90th range covers 80% of true values (nominal 80%) after calibration on the held-out engines (`uv run task calibrate-rul`: conformalised quantile regression, one margin per side; the lower bound moves 1.8 cycles, coverage was 73%). RUL B RMSE 22.3, but 7.6 when true RUL is 25 cycles or less.
- **Safety of the planning bound:** at or below the true RUL for 92% of engines (90% before calibration). Of the 45 engines within 15 days of failure, 1 overshoots. The remaining overshoots are mostly on engines with plenty of life left.
- **Disagreement flag:** 54 engines (27%); RUL A error there is 11.6 vs 7.5 elsewhere.
- **Fault mode (FD003):** 97% agreement with the proxy label; 100% within 60 cycles of failure.
- **Evidence:** 3,401 claims, all with resolvable evidence; every claim that states a quantity cites model, query, rule or case-memory evidence.
- **Seeded faulty briefs:** 19 fault types (uncited claim, tampered or averaged RUL, fabricated part, inflated stock, slot not free, deadline past the RUL bound, mandate dropped, a changed number in a supporting claim, an ignored inspection finding, a dropped or late method of compliance, and others), applied to 30 finished briefs: 357 of 357 caught by the deterministic gates, 0 false alarms on the clean briefs. The first version of the gates missed the tampered RUL, fabricated part and inflated stock faults; the eval found them and two rule checks were added. The changed-number fault got through until the number check was added on 25 Oct; building that check also found a claim citing the wrong evidence, now fixed.
- **Implausible input data:** 0 of the 200 clean runs raise a data-quality issue. 12 corruptions (a T30 spike, P30 in kPa instead of psia, a frozen Ps30, missing phi readings, duplicate, reversed and truncated cycles, a removal on an active engine, negative stock as serious; a cycle gap, an out-of-range T2 the models ignore and a work order after the last cycle as minor) fed through the full graph on 10 engines: 116 of 116 graded at the expected severity, no crashes. Building these found two crashes, now fixed: missing readings broke the telemetry summary, and two specialists failing in the same step broke the graph state.

### Gates against Nemotron alone, on the same briefs

`uv run task eval-llm` (needs Nightshift; `evaluation/llm_critic_report.md`, 25 Oct) takes the four golden briefs, corrupts each with every seeded fault that applies, and sends them to the Nemotron critic with the deterministic gates bypassed. The same golden-brief and fault pairs, rebuilt on 26 Oct, went through the gates:

| Same 42 corrupted golden briefs, plus the 4 clean ones | Faults caught | Clean briefs blocked |
| --- | --- | --- |
| Deterministic gates (rule checks, then the critic's code checks) | 42 of 42 (38 at the rule checks, 4 at the critic's checks) | 0 of 4 |
| Nemotron alone, gates bypassed | 3 of the 41 it answered, by a challenge code confirmed | 0 of 4 |

Nemotron also flagged a problem in free text on 34 of the 41, but free text cannot be checked, and code refuted 147 of its 150 checkable challenges. That is why code decides: the model's notes are shown to the engineer, and only a challenge that code confirms sends a brief back. The gates' side runs with no credentials: `uv run pytest tests/test_judge_features.py -k nemotron_alone`.

## Research findings

`docs/research-findings.md` (26 Oct 2026) lists the market and regulatory findings that point to changes in Cleris, each with its sources and its status here (built, partly built, not built). `docs/regulatory-requirements.md` maps EASA, FAA, ICAO and industry requirements for an operator using a tool like Cleris: 26 requirements, 2 met, 15 partly met, 9 not met, with the actions for a pilot and for operational use. `docs/competitive-landscape.md` compares Cleris with more than 30 products (engine-maker monitoring, airframer platforms, maintenance software, industrial copilots, startups), and `docs/value-model.md` models the value in USD for a 100-aircraft airline from published costs: about $133k per removal turned from unscheduled into planned, break-even at about one extra planned removal a year, modelled and not measured (`uv run python docs/value_model.py` reproduces every figure). The items with the most weight:

- **Sign-off:** a return to service is signed by a certificated person (14 CFR 43.9; EASA Part-145.A.50). The Approve record now names the signer (typed, not authenticated) and binds it to the brief by a hash; the release stays with certifying staff. Next: single sign-on against the operator's authorisation list.
- **Wording:** "clearance" and "release" are regulated words. The README and the UI now carry a "not for operational use" statement and define "clearance" as the engineer's keep, inspect or remove decision; the product line itself is unchanged.
- **Uncertainty and cost:** weight RUL A's range against late removal (asymmetric conformal intervals), and set the inspect and remove thresholds from what each kind of false alarm costs.
- **Data:** read a real M&E system through mapped connectors; today the SQL names the team's own synthetic tables.

## Live mode

The Zetaris cluster, the Postgres databases (AWS RDS) and the S3 bucket stay up in the cloud through judging (28 Oct–6 Nov). Live mode needs credentials, which are not in this repo; sample mode needs none.

### Check the live path without credentials

Live mode needs our Zetaris, AWS and Nightshift credentials, so a judge cannot run it. What it did is committed in `trace_examples/`, and one command reads it back with no credentials and no network:

```bash
uv run task live-evidence
```

- **Five live briefs** (`trace-live-*.jsonl`, 26 Oct 2026): the four golden cases and the beyond-limits inspection case. For each run it lists the Zetaris reads (39 in all: live SQL on `telemetry_pg`, `cmms_pg` and `knowledge_pg` in RDS Postgres and `s3_files` on S3, with tables, rows and timing), the 49 World Model and H-MEM calls to the Meterless context service, and the model that answered the critic through Nightshift (`nvidia/nemotron-3-ultra-550b-a55b`, a live call, not a replay) with how many of its challenges code confirmed or refuted. It checks that every trace reads as a live run: no snapshot fallback, no SQLite stand-in, no replayed or non-Nemotron critic reply.
- **A live `/health` snapshot** (`health-live.json`, 26 Oct 2026 15:39 UTC): `GET /health` read right after one more live brief on FD001-A031 in the same process. Every component is green: Zetaris and each of its four sources (0.6–1.3 s), the context service (World Model 112 edges, H-MEM 202 memories), Nightshift serving Nemotron 3 Ultra 550B A55B, and the CMMS write path. That brief took 25.9 s, with nothing degraded. The file holds component states only, no hosts, URLs, tokens or keys; the capture refuses to write one. `uv run python -m tools.live_evidence --capture-health` retakes it in live mode.
- **A replay page, nothing to install:** https://esutedjo.github.io/MaintenanceAssistant/ shows six live runs recorded on 26 Oct 2026, 17:32–17:38 UTC, one per file in `input_examples/`, with every service green in the `/health` read taken right after them. For each: the brief, the evidence behind every claim (coloured by where it came from), the gates and their verdicts, each Zetaris query with its tables, rows and timing, each World Model and H-MEM call, each Nemotron reply with its challenges confirmed or refuted by code, the full timeline and the decision log. The page is one self-contained file (`docs/index.html`, also openable from a clone) built from the committed run files in `docs/replay/data/` by `uv run task replay`; `uv run python -m tools.replay --record` re-records them in live mode and keeps only runs with nothing degraded.
- **The demo video:** the live run on FD001-A031 with the services strip at 1:44; the Zetaris reads, H-MEM and the Nemotron model in the trace at 2:48.

These files show what the services answered on the day; they do not replace a run. To run the same path on your own stack, see below.

1. Start Nightshift. Put the Console runtime token in `METERLESS_RUNTIME_TOKEN`. Add provider keys (including NVIDIA) in Nightshift **Setup → Providers**, not in this repo. After the first live run, the app `turbofan-maintenance-critic` appears in Nightshift; give it a Judge lane with a Nemotron model.
2. Start the context service: once `npm ci --prefix context-service` (Node 24), then `uv run task context-service` (port 8710). Load once with `uv run task load-context` (idempotent).
3. Set `SAMPLE_MODE=false` and `ZETARIS_MODE=live` in `.env.local`.
4. `uv run python run.py --question "..."` — Zetaris is queried first; on failure the run switches to the snapshot and every later evidence record is flagged `degraded`.

### Run the live path on your own stack

Our cloud (AWS RDS, S3, the Zetaris cluster) needs our credentials, which are never in this repository. Everything needed to rebuild the same sources on your own machine and your own Zetaris is here:

1. **Data, locally.** `uv run task download` (NASA C-MAPSS; eval answer key included), then `uv run task db-up` (Postgres 17 with pgvector on `127.0.0.1:5433`, databases `telemetry`, `cmms`, `knowledge`). Create the two roles the loaders grant to, with passwords of your choice:
   `docker compose exec postgres psql -U postgres -c "CREATE ROLE zetaris_ro LOGIN PASSWORD '<read-only password>'; CREATE ROLE app_rw LOGIN PASSWORD '<write password>';"`
   Then `uv run task seed-telemetry --target local`, `uv run task seed-cmms --target local` and `uv run task ingest-docs --target local`. The answer key (`eval_ground_truth`) is revoked from both roles.
2. **Failure modes on S3.** `uv run task fmea` writes `data/export/fmea.parquet`; upload it to a bucket of yours under `fmea/`. Zetaris cannot read a file on your laptop.
3. **Zetaris.** In your Zetaris (Docker or AWS, per the hackathon setup guide), create three JDBC sources named `telemetry_pg`, `cmms_pg` and `knowledge_pg` that point at the local Postgres as `zetaris_ro` (from a Zetaris container, the host is `host.docker.internal:5433`), and a file source `s3_files` with the table `fmea` from your bucket. Put `ZETARIS_BASE_URL`, `ZETARIS_ORG_ID` and `ZETARIS_API_KEY` in `.env.local`, then `uv run task register` imports the tables and checks that each source returns rows.
4. **Meterless.** Once `npm ci --prefix context-service` (Node 24), then `uv run task context-service` (port 8710), then once `uv run task load-context`. In Nightshift, put the runtime token in `METERLESS_RUNTIME_TOKEN` and your NVIDIA key in Nightshift's Providers tab; after the first live run, give the app `turbofan-maintenance-critic` a lane with a Nemotron model.
5. **Run.** In `.env.local`: `SAMPLE_MODE=false`, `ZETARIS_MODE=live`, and for the work-order write `PG_HOST=127.0.0.1`, `PG_PORT=5433`, `PG_SSLMODE=disable`, `PG_APP_USER=app_rw` with its password. `uv run task preflight` checks every service and runs one live brief; the UI's services strip shows each one.

**What we tested on 26 Oct 2026, and what we did not.** Tested on a Windows machine with Docker Desktop: step 1 with the C-MAPSS files already downloaded (the download itself was not re-run for this test), `db-up`, both roles, `seed-telemetry`, `seed-cmms` and `ingest-docs` with `--target local` (75,043 cycle rows for 400 engines; 400 assets and 1,577 work orders; 19 document sections including the 6 limit tables), and `fmea` (16 rows). As `zetaris_ro`, every table Zetaris needs is readable, while the answer key and any write are refused; as `app_rw`, a draft work order inserts (rolled back after the test). Not tested by us: steps 3 to 5 against a local stack, that is, registering a local Postgres and an S3 file in a Zetaris that can reach them and running a live brief end to end on it, because our Zetaris runs in AWS and cannot reach a database on a laptop. The same registration, context-service, Nightshift and preflight steps are the ones our cloud setup runs (`uv run task preflight`: every service passes).

`uv run task check-llm` confirms Nightshift. `uv run task check` confirms RDS and Zetaris. Before recording a demo, `uv run task preflight` checks every live dependency (mode, Zetaris, context service, the Nemotron critic lane) and runs one live brief; it writes nothing.

### Back up and restore the live data

`uv run task backup --verify` makes a portable copy of the cloud data. It needs the loader settings in `.env.local` and Docker. By default it writes outside the repository, to `../backups/cleris-<UTC date>/`, and it refuses a folder inside it. It only dumps and reads; it writes nothing to RDS or S3.

| File | Content |
| --- | --- |
| `telemetry.dump`, `cmms.dump`, `knowledge.dump` | `pg_dump` custom-format archives from RDS, made with pg_dump 17 from the image in `docker-compose.yml` |
| `fmea_from_s3.parquet` | The FMEA table as Zetaris serves it from S3 (`s3_files.fmea`) |
| `manifest.json` | Per database: tables, row counts, file size, SHA-256 and server version; the repository commit |
| `RESTORE.txt` | The restore commands and grants below |

No host, password or API key is printed or written, and a failed `pg_dump` reports only its exit code. The archives name object owners by role, as every pg_dump archive does, so restore with `--no-owner`. Grants are left out of the dumps. `--verify` restores each archive into a scratch database on the local Docker Postgres, compares every table's row count with the manifest, and drops the scratch databases.

Restore into the local Postgres (`uv run task db-up` creates the three databases), once per database:

```bash
docker compose exec -T postgres pg_restore -U postgres -d telemetry --clean --if-exists --no-owner --no-privileges < ../backups/cleris-<date>/telemetry.dump
```

For a new PostgreSQL 17 with pgvector (a new RDS instance, for example), create the database first, then run `pg_restore -h <host> -U <admin user> -d <db> --no-owner --no-privileges <db>.dump`. In both cases, re-grant to the two roles; the answer key `eval_ground_truth` stays unreadable to both:

```sql
-- telemetry
GRANT SELECT ON cycles, signals, telemetry_long TO zetaris_ro;
-- cmms
GRANT SELECT ON ALL TABLES IN SCHEMA public TO zetaris_ro;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_rw;
-- knowledge
GRANT SELECT ON doc_chunks TO zetaris_ro;
```

Then upload `fmea_from_s3.parquet` to your bucket under `fmea/`, point the Zetaris sources at the restored copies, and run `uv run task register`.

The backup does not include:

- credentials;
- the Zetaris source definitions;
- the Nightshift lanes and provider keys;
- the Meterless store. `uv run task load-context` rebuilds it from the repository, but sign-off memories written since then are not restored.

In AWS itself, an RDS snapshot and S3 versioning are the faster way back. This backup is the copy that runs anywhere.

**Tested on 26 Oct 2026.** We made the backup from our RDS. It holds 3 telemetry tables (75,464 rows), 8 cmms tables (10,314 rows), 1 knowledge table (19 sections) and 16 FMEA rows. `--verify` matched every row count. We also ran the restore command above twice into scratch databases, the second time with `--clean`, and applied the grants. After that, `zetaris_ro` reads `cycles`, `telemetry_long` and `doc_chunks` but is refused the answer key and any write. `app_rw` can update `work_orders` and is refused the answer key. Not tested: a restore into a new RDS instance, and the Zetaris re-registration against a restored copy.

## Commands

| Command | What it does |
| --- | --- |
| `uv run task test` | Unit tests |
| `uv run task eval` | Fleet evaluation, decisions against the answer key, seeded faulty briefs and corrupted inputs (`evaluation/report.md`) |
| `uv run task fleet-triage` | Run the agent graph on all active engines and rank them by decision and deadline (`data/snapshot/fleet_triage.parquet`) |
| `uv run task eval-llm` | Nemotron critic on its own against the seeded faults (needs Nightshift; `evaluation/llm_critic_report.md`) |
| `uv run task preflight` | Read-only check of every live dependency plus one live brief, before recording a demo |
| `uv run task replay` | Rebuild the no-install replay page (`docs/index.html`) from the committed live runs in `docs/replay/data/` |
| `uv run task live-evidence` | Summarise and check the committed live traces and the live `/health` snapshot; no credentials or network |
| `uv run task backup --verify` | Portable backup of the live data (RDS dumps, FMEA from S3, manifest) outside the repository, checked by a local restore |
| `uv run task calibrate-rul` | Calibrate RUL A's 10th–90th range on the held-out engines (stored in `rul_a.json`) |
| `uv run task sensor-ranges` | Plausible sensor ranges and steps from the training engines (`asset_packs/.../sensor_ranges.json`) |
| `uv run python run.py --question "..."` | One decision brief as JSON |
| `uv run task ui` | Streamlit decision brief (approve / reject, fleet, trace, asset pack) |
| `uv run python run.py --serve` | `POST /run` on `RUN_HOST:RUN_PORT` (`GET /health` included) |
| `docker build -t cleris .` then `docker run --rm -p 127.0.0.1:8000:8000 cleris` | Sample-mode API, no credentials; runs as a non-root user |
| `uv run task check` | Read-only connectivity check (live credentials) |
| `uv run task check-llm` | Nightshift `GET /v1/models` plus one chat request |
| `uv run task context-service` | Meterless World Model + H-MEM on 127.0.0.1:8710 (once before: `npm ci --prefix context-service`) |
| `uv run task load-context` | Load asset pack + 200 past cases into the context store |
| `uv run task snapshot` | Rebuild `data/snapshot/*.parquet` (`--verify` compares with Zetaris) |
| `uv run task ingest-docs` | Chunk and embed the asset pack's documents into `knowledge.doc_chunks` (pgvector) and `data/snapshot/doc_chunks.parquet` (`--target none` for the snapshot only) |
| `uv run task train-rul` | Remaining-life methods A (LightGBM) and B (health-index trend) |
| `uv run task train-fault-mode` | Calibrate the signature matcher |
| `uv run task train-anomaly` | Fleet health-index curve for the anomaly flag |
| `uv run task download` | NASA C-MAPSS FD001/FD003 into `data/raw/` (not committed) |

## Sponsor tech

- **Zetaris:** all structured reads go through the AI Data Gateway REST SQL API (`tools/zetaris.py`), guarded to `SELECT` only. Snapshot fallback when it is down.
- **Meterless:** (1) Nightshift is the only model provider (`tools/llm.py`): free-text intake, the evidence critic and the Ask Cleris explainer, all checked by code. (2) The World Model holds the asset graph; the maintenance planner reads which tasks address the fault mode. (3) H-MEM holds past removals. Diagnostics cites similar cases and the median warning those cases gave; the engineer's approve/reject updates their trust. Both engines run in `context-service/`. SQLite is the fallback when that service is down. Engine tests, in `context-service/` after `npm ci`: `npm test` (the service on both engines, 8 of 8) and `npm run conformance` (the official Meterless conformance suites on the vendored engines: H-MEM 13 of 13, World Model 8 of 8). `tests/test_world.py` checks the planner's World Model queries on the SQLite fallback, and `tests/test_context_store.py` the store itself.
- **NVIDIA:** one Nightshift app whose lane is pinned to an NVIDIA Nemotron model (the NVIDIA key lives in Nightshift Providers, not in this app) does three jobs, each checked by code. (1) Free-text intake: when the parser finds no engine ("the engine with serial 700131", "engine thirty-one of the first fleet"), Nemotron names it and quotes the words; code accepts it only if the quote is in the question, contains the engine's number, and the engine is in the registry (`agents/intake.py`). Sample mode replays this for one exact question (see Sample mode). (2) The critic, whose checkable challenges change the outcome only when code confirms them. (3) Ask Cleris in the help panel: Nemotron explains the brief from the fields on screen, and code flags any number, date or ID in its answer that the brief does not contain. The planner is code. The lane runs **Nemotron 3 Ultra 550B A55B** (`nvidia/nemotron-3-ultra-550b-a55b`); on the four golden cases it answered in 6–37 s and the trace records it as the critic model. Other hosted Nemotron models did not work for this account on 23 Oct (empty responses, the 45 s Nightshift provider limit, not entitled, or retired). If the critic does not answer, the deterministic checks carry the verdict and the brief says `LLM critic unavailable`.
- **Cursor:** this repository was developed in Cursor. Claude Code was also used from 25 Oct for fixes, verification and the release (handoff notes in `CLAUDE.md`).

## Limitations

- **Data:** NASA C-MAPSS simulated telemetry (two fleets, FD001 and FD003, two fault modes). All maintenance, spares, hangar-slot and document data is synthetic, generated by the team. The planning reference date is fixed at 2026-10-22 to match it.
- **Scope:** one engine type. The asset pack separates engine-specific data from the agents, but a second machine type has not been built.
- **Not certified, not for operational use.** Cleris drafts a work order; it never issues a release to service. The signer's name is typed, not authenticated, and records are local files with a hash chain but no backup or retention policy (see `docs/regulatory-requirements.md`).
- **Caution has a cost:** of the 102 engines with more than 40 days left, 41 are sent for an inspection and 1 is pulled early. The inspection-findings loop resolves inspections; it does not avoid them.
- **Models:** RUL B's range is not calibrated (45% coverage); fault-mode accuracy (97%) is against a proxy label from one sensor; the seeded-fault results (357 of 357) are a self-test of the failure classes the gates were built to catch.
- **Live mode** needs the team's cloud: a Hobby-tier Zetaris cluster with random 15–170 s stalls (the run then falls back to the snapshot and says so), AWS RDS and S3, and the Nightshift app. Judges run sample mode, where Nemotron's part is the recorded replies; `uv run task live-evidence` reads back the committed live runs and a live `/health` snapshot.
- **Integration:** no connector to a real M&E system (AMOS, TRAX, SAP PM, Maximo); the SQL names the team's own tables.

## Pre-existing components

- NASA C-MAPSS FD001/FD003 (simulated turbofan run-to-failure). Not committed; `uv run task download`.
- Meterless H-MEM and World Model reference engines, vendored at commit `1742dec` under Apache License 2.0. See `context-service/vendor/VENDORED.md`.
- Failure physics follow Saxena, Goebel, Simon, Eklund (2008). The FMEA table and all CMMS / planning data are generated and labelled synthetic.
- A design document written on 15 Oct 2026, before the build window (planning only, no code).
- Photos in `asset_packs/turbofan_cmapss/images/` are from Wikimedia Commons, used unmodified under their licences (public domain, CC BY 2.0, CC BY-SA 2.0/3.0). Author, licence and source page for each are listed under `images` in `manifest.yaml` and shown under each photo in the UI. They illustrate the engine class (mostly CFM56), not the simulated units. The engine diagram is drawn from the asset pack's `diagram` section.

## Traces

Each run writes `logs/trace-<run_id>.jsonl` (timestamp, run_id, agent_name, action, input_summary, output_summary, target_agent, model, confidence, retry_count, status). The served model name is logged for every LLM request.

Committed examples in `trace_examples/`:
- `trace-sample-*`: the four golden cases in sample mode, plus FD001-A031 with a corrupted T30 reading (serious: `data_quality` escalation, confidence low) and with a gap in the cycles (minor: flagged only), regenerated on 26 Oct. The critic lines replay recorded Nemotron replies where the prompt matches, marked `recorded: true`.
- `trace-live-*`: the four golden cases and the beyond-limits inspection case in live mode, recorded on 26 Oct with the current graph: one `zetaris_query` line per read (purpose, source tables, rows, latency, "live Zetaris SQL"), one `context_call` line per World Model or H-MEM call (backend `meterless`), the planner's follow-ups and routing, and the critic answered through Nightshift by Nemotron 3 Ultra 550B (`model` on the critic line, `recorded: false`). Each run took 6–31 s.
- `health-live.json`: `GET /health` in live mode right after one more live brief, all components green (26 Oct). `uv run task live-evidence` summarises and checks these files.

## Licence

Apache License 2.0, see [LICENSE](LICENSE). The vendored Meterless reference engines keep their own Apache-2.0 licence (`context-service/vendor/VENDORED.md`). The NASA C-MAPSS data is downloaded by `uv run task download` and is not part of this repository.
