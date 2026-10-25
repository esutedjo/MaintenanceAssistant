# CLAUDE.md — continue from 25 Oct 2026

Handoff from a Cursor session. Read `AGENTS.md` for the architecture and the rules that do not change. This file is what changed in that session, what was only discussed, and what not to undo.

The product name is **Cleris, the MRO clearance assistant**. The hackathon track stays **The Agent That Can Explain Why**. Do not rename the track line, the GitHub repo, or the Python package `maintenance-assistant`.

This session's work was committed and pushed to `main` on 25 Oct. Do not commit unless the user asks. Do not read `.env.local` or any secret file. Code freeze in `AGENTS.md` is 27 Oct 2026 12:00 UTC.

The GitHub branch `claude/magical-cannon-jlrs9d` is a parallel line from a Claude Code web session, built on the 23 Oct `main`: a separate sign-off write service and runner streaming. It is not merged and overlaps files changed here. Its `merge_evidence` fix (keep the first record of an ID) was ported to `main`.

## What Cleris is

An MRO clearance is the stay-or-remove decision an engineer can sign: the condition, the remaining-life limit, the eligible work, and the signature. Cleris writes that clearance. It does not replace Siemens Senseye, IBM Maximo Assistant, or Cognite Atlas. It sits on the systems the shop already has.

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

Name surfaces updated after the tests: `README.md`, `AGENTS.md`, `pyproject.toml` description, `ui/app.py` (browser title **Cleris**, sidebar caption "the MRO clearance assistant"). The documented Docker tag is `cleris`. The package name is still `maintenance-assistant`.

`output_examples/` and `trace_examples/` were generated before features 1–7. They still say "FMEA task" where the planner now says "World Model task". Do not treat those files as the current wording until they are regenerated with `SAMPLE_MODE=true`. `tests/test_graph.py` checks behaviour (swap on FD001-A031, borescope on FD001-A070, "World Model:" in the evidence summaries), not the old sentence text.

## Claims that must stay true

- The planner still *selects* among the graph's tasks by severity, inspection preference, mandates, and the lead-time rule. The graph supplies the eligible set. It does not rank them and it does not yet refuse a task inside an agent that bypasses rule checks.
- H-MEM similar-case search retrieves past removals tagged `case`, not the sign-off memory. Sign-off updates trust on the cited past cases (`helpful` +0.05, `not_helpful` −0.03, `wrong` −0.20 and a `review` tag). The asset facts in item 5 are the place the next run sees *this* engine's last decision.
- On FD003-A100 the second critic round is the deterministic driver check (Nc, NRc, htBleed move under every fault; the graph links fan degradation to the fan). It is not the Nemotron call. Nemotron notes on a fully cited brief are shown and do not veto.
- FD001-A031, from the last committed sample output: RUL A 9.8 (7–11), RUL B 6.7, planning lower bound 0 cycles, deadline 2026-10-22, next slot 2026-10-23, parts on hand, action is the lease-engine swap, AD-2026-17-04 keeps FM-HPC-01, H-MEM median warning 27 cycles across 127 HPC removals, this engine is 16 cycles past its first moderate finding, confidence stays high. Re-measure if you regenerate outputs.
- Zetaris does not map sensors to components. That mapping is the asset pack, now also the World Model `monitors` edges. The essential Zetaris query is `s3_files.fmea` joined to `cmms_pg` stock and lead time. The app holds no database password. The eval answer key is revoked from the Zetaris user. Do not name the product, or a slide, "Ground Truth".
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

1. **Market contrast in the pitch.** The user asked for a unique-value argument against current MRO products (federated systems of record versus a platform the plant must migrate into). The session was interrupted before any slide or script line was written. Senseye, Maximo Assistant, and Cognite are already named in `AGENTS.md` as products we do not claim to precede. A fair contrast, when written: they advise from a model or a ticket; Cleris will not issue a clearance whose work is off the machine graph or whose parts were not read from the live systems. Do not invent capabilities those products lack unless you have checked.

2. **MRO process, said as the use case.** Slide 2 already says "Airline or MRO shop". A pass that frames the whole story as the MRO process (on-wing disposition, induction, shop visit, release) was started and not finished.

3. **Further World Model work, in the order already agreed, after 1–7.** Not built: writing a memory when the task list itself changes; a second asset type in the same world; an operator edit of an `addresses` edge that the next run must obey with no code change. Also not built: putting `pack_version` on the task-list evidence record (it is stored on the asset at sign-off only).

4. **Renaming pitch filenames.** Content says Cleris. Filenames still say HanSolo and ExplainWhy. Leave them unless the user asks. Team name on the materials remains Team Han Solo. No franchise imagery, ships, fonts, or music.

## If you continue

Regenerate `output_examples/` and the sample traces only when the user wants the committed examples to match the graph-backed planner. Until then, say they are older.

The pitch gap that matches the judging rubric is the unfinished market contrast, written only from behaviour that is already true: one read-only join for parts, eligible work from the World Model, a critic that cannot change a number, and a clearance the engineer signs. Put that in the VALUE deck and the video script, not in a new code path.

Verify with `uv run pytest -q` from the repo root. Sample mode needs no credentials.
