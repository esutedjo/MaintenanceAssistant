# CLAUDE.md — continue from 25 Oct 2026

Handoff from a Cursor session. Read `AGENTS.md` for the architecture and the rules that do not change. This file is what changed in that session, what was only discussed, and what not to undo.

The product name is **Cleris, the MRO clearance assistant**. The hackathon track stays **The Agent That Can Explain Why**. Do not rename the track line, the GitHub repo, or the Python package `maintenance-assistant`.

This session's work was committed and pushed to `main` on 25 Oct. Do not commit unless the user asks. Do not read `.env.local` or any secret file. Code freeze in `AGENTS.md` is 27 Oct 2026 12:00 UTC.

The GitHub branch `claude/magical-cannon-jlrs9d` was a parallel line from a Claude Code web session, built on the 23 Oct `main`: a separate sign-off write service and runner streaming. Its `merge_evidence` fix (keep the first record of an ID) was ported to `main`; the rest was not merged. The branch was deleted from GitHub on 25 Oct. Its two commits are kept in the developer's local clone as `refs/backup/claude-magical-cannon-jlrs9d` (not a branch, not pushed).

## State on 26 Oct 2026 (supersedes the 25 Oct numbers below)

The sections after this one are the 25 Oct handoff and keep their dates. What changed on 26 Oct is in `AGENTS.md` (follow-ups and gap routing, recorded Nemotron replay, decision log, inspection-findings loop, help panel, services strip, hardening, and the "MRO engineer review" changes). Current numbers, all sample mode:

- `uv run pytest -q`: 424 passed (27 Oct, after the trace-writer lock: parallel agents had occasionally written over each other's trace lines on Windows, 63 of 9,733 local trace files; the cause of the one-off `test_intake_llm` failure).
- `uv run task eval`: 357 of 357 seeded faulty briefs caught (19 types), 0 false alarms; 3,401 claims, all resolvable and number-supported; 45 of 45 engines failing within 15 days sent for removal, swap or repair; RUL A range coverage 80%; planning bound at or below true RUL for 92%; 116 of 116 corrupted inputs graded as expected; `sb_adoption` on 134 of 200 engines.
- FD001-A031: RUL A 9.8 (5–11), RUL B 6.7, planning bound 0, "Do not dispatch; swap in lease engine at the first free slot (2026-10-23)", AD-2026-17-04 (g) complied with by removal before the limit, work package 58.5 manhours with 4 RII.
- Use the corrected competitor line: Maximo Assistant, Senseye and Cognite Atlas answer from data held in, or copied into, their own platforms; Cleris is built to query the shop's existing systems where the data lives (`docs/competitive-landscape.md`).
- `uv run task live-evidence` (`tools/live_evidence.py`) summarises and checks the committed live traces and `trace_examples/health-live.json`, a live `GET /health` read right after one live brief on 26 Oct 15:39 UTC (every component green). Judges need no credentials for it. Retake with `uv run python -m tools.live_evidence --capture-health` in live mode; it refuses to write a setting's value, a URL or an AWS host.
- No-install replay: https://esutedjo.github.io/MaintenanceAssistant/ (GitHub Pages from `main` `/docs`). `docs/index.html` is built by `uv run task replay` from six live runs in `docs/replay/data/` (26 Oct 17:32–17:38 UTC); `uv run python -m tools.replay --record` re-records them in live mode. `tests/test_replay.py` fails if the page is stale.
- Model roles: critic, intake and Ask Cleris each call Nightshift through their own app setting (`settings.lane(role)`: `METERLESS_{CRITIC,INTAKE,EXPLAIN}_APP` and `_MODEL`). All three stay on `turbofan-maintenance-critic` (Nemotron lane): on 26 Oct evening the user tried Gemini 3.7 Flash and Claude 4.5 Sonnet lanes; Gemini's key ran out of quota after a few calls, Claude was reachable only by winning Nightshift's race, and lane edits briefly left every app with no provider. Kept Nemotron-only. App rules and lanes can only be edited in the Nightshift desktop app (the paired web view on port 8089 has no rule editing). `check-llm --repeat N` and preflight report model, provider, cost and Nightshift receipt ID per role.
- A portable backup of the live data (RDS dumps, FMEA from S3, manifest) is made with `uv run task backup --verify`, outside the repository (README, "Back up and restore the live data").

## What Cleris is

An MRO clearance is the stay-or-remove decision an engineer can sign: the condition, the remaining-life limit, the eligible work, and the signature. Cleris writes that clearance. It does not replace Siemens Senseye, IBM Maximo Assistant, or Cognite Atlas: those answer from data held in, or copied into, their own platforms, while Cleris is built to query the shop's existing systems where the data lives.

Say "maintenance release", not an air-traffic clearance, the first time the name meets a general audience.

## Code that landed in this session

The World Model is now the runtime source of eligible work. Zetaris still prices parts, slots, telemetry, history, and documents. Numbers are still computed in Python or SQL. The critic still cannot overwrite a cited number.

| # | Behaviour | Where |
| --- | --- | --- |
| 1 | The planner takes tasks from `failure_mode → addresses → task`, including part numbers on the task. `tools.parts()` is the stock and lead-time call. An empty graph does not invent a task. | `agents/maint_planner.py`, `casefile/world.py` |
| 2 | Rule checks fail a `task_id` that is not on an `addresses` edge for the claimed fault. A brief with no World Model evidence is left alone, so unit tests of isolated recommendations still pass. | `assurance/rules.py` `world_tasks_eligible` |
| 3 | On a two-mode fleet the critic still requires a discriminating sensor. With the store up it also walks `sensor → monitors → component` and `fault → affects → component`. | `agents/critic.py` `drivers_vs_fault` |
| 4 | Live FMEA task ids that the graph does not list become escalation `knowledge_drift`. The graph stays the work list. | `assurance/escalation.py` |
| 5 | Approve and reject write `last_decision`, `last_fault`, `last_bound_cycles`, `last_action`, `pack_version`, and `last_case_id` on an `asset` node, plus `instance_of` the asset type when that node exists. The next brief can cite that sign-off. | `tools/writeback.py`, `casefile/world.py` `signoff_batch` |
| 6 | Document sections that name a task are `document` nodes with `requires → task` and `applies_to → failure_mode`. Knowledge still decides *when* a mandate fires from RUL and probability. The graph answers *which* task the document requires. | `casefile/load_context.py` `_add_documents` |
| 7 | `addresses` and `affects` are scoped to `FD001` or `FD003`. Fan tasks are invisible on FD001. A document that applies to two faults in one fleet uses the context `{variant}\|{mode}` so edges stay single-valued. | `casefile/load_context.py`, `casefile/world.py` `context_ok` |

`context-service/src/engines.ts` and the SQLite store now return `context` on each edge. Query helpers live in `casefile/world.py`. Tests are `tests/test_world.py` plus the eligibility case in `tests/test_rules.py`. After that work, `uv run pytest -q` was **181 passed**. The full suite was re-run after the rename to Cleris on 25 Oct, again 181 passed.

Name surfaces updated after the tests: `README.md`, `AGENTS.md`, `pyproject.toml` description, `ui/app.py` (browser title **Cleris**, header line "the MRO clearance assistant"). The documented Docker tag is `cleris`. The package name is still `maintenance-assistant`.

`output_examples/`, the four golden `trace-sample-*` files and the four `trace-live-critic-*` files were regenerated on 25 Oct on the graph-backed planner (live: Zetaris, context service, Nemotron critic, 6–14 s per run). The two `trace-sample-bad-data-*` files are still from 23 Oct. `uv run task eval` was re-run on 25 Oct: same headline results, 2,907 claims.

Two fixes from the 25 Oct live runs: `casefile/world.py` lists each task once (a store loaded before edges were scoped keeps an unscoped and a scoped `addresses` edge, which doubled the list in live mode only), and the live history query leaves out the app's own `draft` work orders (two test drafts on FD003-A100 from 23 Oct made the plausibility gate flag "work orders dated after the reference time"; they were deleted from `cmms.work_orders` on 25 Oct at the user's request, and no drafts remain). `tests/test_graph.py` checks behaviour (swap on FD001-A031, borescope on FD001-A070, "World Model:" in the evidence summaries), not the old sentence text.

## UI and judge-facing features (25 Oct, evening)

- **Layout**: no sidebar. The question, Run, the four example engines and the Explain switch sit at the top of the page. Under 1024 px the key numbers go 2 x 2 and the method cards stack (keyed containers `grid2-*` and `stack-*`, CSS in `ui/brief.py`). The tab bar was hidden under Streamlit's header before; the top padding fixes it. Deploy is hidden by `toolbarMode = "minimal"`.
- **Brief**: verdict, check chips (`check_summary`), escalation cards (`escalation_view`), key numbers, plan card with the World Model eligibility (`world_eligibility`), **Why this plan and not another** (`decision_boundaries`: severity band, swap against lead time plus slot, disagreement, mandates, computed from the planner's decision record), In plain words (folded), sign-off (past-case ratings folded), five detail tabs.
- **Trace tab** is a timeline (`trace_rows`). **Stress test tab** applies the 16 seeded faults of `evaluation/faults.py` to the brief on screen and shows which gate stops each (`ui/stress.py`). On the golden cases every applicable fault is caught and the clean brief passes.
- **Fleet triage**: `uv run task fleet-triage` (`tools/triage.py`) runs the full graph on all 200 active engines in sample mode (about 75 s) and writes `data/snapshot/fleet_triage.parquet` plus a timestamp. The Fleet tab ranks them and can recompute. A fleet question on the brief page ("Which engines need attention this week?") shows the engines due within 7 days.
- **Intake** accepts `FD003 A100`, `fd001_a031` and "engine 31 in the FD001 fleet"; two named engines ask which one; a fleet question gets `FLEET_QUESTION`.
- **Eval** has a decision table against the answer key: 45 of 45 engines that fail within 15 days were sent for removal or swap; of 102 with more than 40 days left, 1 was pulled early and 41 got an inspection; of 53 in between, 1 was told to keep flying. VALUE deck slide 12 shows 45 / 45 in place of "200 of 200 runs".

## Late 25 Oct: measures for a skeptical judge

- **Nemotron intake** (`agents/intake.py`): the parser first; only when it finds no engine (or the wording sounds like a fleet question) Nemotron, on the critic's Nightshift lane, names the engine and quotes the words. `grounded_engine` accepts it only if the quote is in the question and contains the engine's number (digits or simple number words), the fleet is named, and the registry has the engine. `Intent.resolved_by` and the intake trace line carry the model. Tests: `tests/test_intake_llm.py`.
- **Number check** (`assurance/challenges.py`, rule `numbers_supported`): every number a claim states must be in its cited evidence, a pack threshold, a sum or difference of two headline values (summary or top-level content), or a cycles-to-days conversion. 0 of 2,907 clean claims fail it. Building it found the "cycles past its first moderate finding" claim citing only H-MEM; the planner now stores that number as its own evidence record and cites it. New seeded fault `claim_number_altered` (16 types; 311/311 caught).
- **Critic challenges**: Nemotron returns checkable challenges with its gaps; code confirms or refutes each (`checked_challenges` in `agents/critic.py`). Confirmed ones send the brief back; refuted ones are notes. `uv run task eval-llm` measured Nemotron alone: 34 of 41 corrupted briefs flagged in free text, 147 of 150 checkable challenges refuted, 0 clean briefs blocked.
- **Calibration** (`models/calibrate_rul.py`, `uv run task calibrate-rul`): per-side conformal margins on the held-out engines, stored in `rul_a.json` (lower 1.8 cycles, upper 0). Coverage 73% → 80%; planning bound at or below true RUL 90% → 92%. FD001-A031's A range is now 5–11 (was 7–11). Decisions and triage counts did not change.
- **Preflight** (`uv run task preflight`): read-only check of mode, Zetaris, context service, Nemotron lane and one live brief. It warns when golden engines have draft work orders; after the two test drafts were deleted on 25 Oct it printed "Ready to record".
- **Pitch** (Pitch Material, originals in the session scratchpad): slide 6 is a real screenshot of the live FD001-A031 brief (link `?engine=FD001-A031` opens a brief directly); slides 4, 5, 8, 9, 10, 11, 12 and notes no longer say "the only model call is the critic" and carry the new numbers (311 / 311, 80% coverage, 147 of 150 refuted). The video script is resized to about 3:30 (482 narration words, 8 s opening, a 10 s "try to break it" segment, the 45 / 45 result). `OAH2026-HanSolo-Submission-Form-DRAFT.md` holds the form answers.

## Claims that must stay true

- The planner still *selects* among the graph's tasks by severity, inspection preference, mandates, and the lead-time rule. The graph supplies the eligible set. It does not rank them and it does not yet refuse a task inside an agent that bypasses rule checks.
- H-MEM similar-case search retrieves past removals tagged `case`, not the sign-off memory. Sign-off updates trust on the cited past cases (`helpful` +0.05, `not_helpful` −0.03, `wrong` −0.20 and a `review` tag). The asset facts in item 5 are the place the next run sees *this* engine's last decision.
- On FD003-A100 the second critic round is the deterministic driver check (Nc, NRc, htBleed move under every fault; the graph links fan degradation to the fan). It is not the Nemotron call. Nemotron's free-text notes on a fully cited brief are shown and do not veto; only a challenge code confirms sends a brief back.
- FD001-A031, from the last committed sample output: RUL A 9.8 (5–11), RUL B 6.7, planning lower bound 0 cycles, deadline 2026-10-22, next slot 2026-10-23, parts on hand, action is the lease-engine swap, AD-2026-17-04 keeps FM-HPC-01, H-MEM median warning 27 cycles across 127 HPC removals, this engine is 16 cycles past its first moderate finding, confidence stays high. Re-measure if you regenerate outputs.
- Zetaris does not map sensors to components. That mapping is the asset pack, now also the World Model `monitors` edges. The essential Zetaris query is `s3_files.fmea` joined to `cmms_pg` stock and lead time. Reads hold no database password; only the engineer's sign-off write logs in to Postgres, as the `cmms`-only `app_rw` user. The eval answer key is revoked from the Zetaris user. Do not name the product, or a slide, "Ground Truth".
- Stock, lead time, hangar slots, RUL, and sensor values stay out of the World Model.

## Pitch files (outside the git repo)

`D:\OneDrive\_OFFICE MATTER\2026 Competitions\GenAIWorks - Hackathon\Pitch Material\`

| File | Role |
| --- | --- |
| `OAH2026-HanSolo-ExplainWhy-Deck-VALUE.pptx` | Deck to present. Sponsor slides and speaker notes match the code above. |
| `OAH2026-HanSolo-ExplainWhy-Deck-DRAFT.pptx` | Older deck. The Cleris rename was applied. Prefer VALUE for content. |
| `OAH2026-HanSolo-Video-Script-DRAFT.docx` | Narration. FD001-A031 is the main run. FD003-A100 is only the deterministic second round. |
| `OAH2026-HanSolo-Opening-Photoreal-Shot-Pack-DRAFT.docx` | Short-film assembly notes. |
| `OAH2026-HanSolo-Opening-Animation.html` | 28 s opening. Title card: **Cleris** / **the MRO clearance assistant**. |
| `OAH2026-HanSolo-Opening-Animation-2D-backup.html` | Same title card. |

PowerPoint has locked the draft deck before. If a copy fails, write a sibling file and say so. Do not leave two decks that disagree.

The title card used to carry "The map of the machine. The data. A second opinion." That argument is now spoken in the demo, not printed on the card. The card is the product name.

## Discussed and not done

These were explicit requests or recommendations. They are not in the code and must not be described as built.

1. **Market contrast in the pitch.** Written on 25 Oct as speaker notes (VALUE deck slide 2) and one narration line (video script, 3:00–3:25): Senseye, Maximo Assistant and Cognite Atlas advise from inside their own platforms; Cleris sits on the systems the shop already has, and its rule checks send back any task the machine graph does not list for the fault. No slide text was added. Do not invent capabilities those products lack unless you have checked.

2. **MRO process, said as the use case.** One line in the slide 3 notes frames the brief as the on-wing disposition (keep on wing, inspect, or remove for a shop visit, with the release the engineer signs). A full pass through every slide was not done.

3. **Further World Model work, in the order already agreed, after 1–7.** Not built: writing a memory when the task list itself changes; a second asset type in the same world; an operator edit of an `addresses` edge that the next run must obey with no code change. Also not built: putting `pack_version` on the task-list evidence record (it is stored on the asset at sign-off only).

4. **Renaming pitch filenames.** Content says Cleris. Filenames still say HanSolo and ExplainWhy. Leave them unless the user asks. Team name on the materials remains Team Han Solo. No franchise imagery, ships, fonts, or music.

## If you continue

Before a live demo: start the context service (`uv run task context-service`), run `uv run task load-context`, and keep `ZETARIS_MODE=live` in `.env.local`. Zetaris still stalls now and then (a run falls back to the snapshot and says so); run the take again. Approving a case in live mode writes a draft work order to `cmms.work_orders`.

The 25 Oct pitch pass is in the VALUE deck and the video script: reads-only password wording, slide numbers, FD001-A031 as the live-run question, DRAFT notes replaced, Cursor plus Claude Code, the market contrast. The originals are not kept in the Pitch Material folder.

Verify with `uv run pytest -q` from the repo root. Sample mode needs no credentials.
