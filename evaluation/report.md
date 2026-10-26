# Evaluation report (sample mode, synthetic fleet)

Generated 2026-10-26 00:38 UTC by `uv run task eval`. 200 active engines through the full agent graph on the committed snapshot; scored against the NASA C-MAPSS answer key, which the agents never read.

## Run completion

- Statuses: needs_review 200; crashes 0.
- Time per run: median 0.38 s, 95th percentile 0.44 s, max 0.5 s (limit 120 s).

## Remaining life

- RUL A (LightGBM): RMSE 12.13, MAE 8.6; 10th-90th interval covers 80% of true values (nominal 80%).
- RUL B (health index): RMSE 22.31, MAE 15.72; RMSE 7.55 when true RUL is 25 cycles or less.
- Planning bound (min of A 10th percentile and B lower) is at or below the true RUL for 92.0% of engines. 16 engines overshoot (mean 8.9 cycles); flagged for inspection or low confidence among them: 1. Near failure it matters most: of the 45 engines within 30 cycles (15 days) of failure, 1 overshoot.
- Methods disagree (> 20 cycles) on 54 engines (27%); RUL A error there is 11.61 vs 7.48 elsewhere, so the flag points at genuinely uncertain engines.

## Fault mode

- FD003 accuracy against the proxy label: 97.0% of 100 engines; 100.0% of the 41 engines within 60 cycles of failure (early in life the signature has not separated yet).

## Evidence

- 2961 claims; 100.0% cite evidence IDs that resolve.
- 1951 claims state a quantity (identifiers such as document sections excluded); 100.0% cite model, query, rule or case-memory evidence computed in code, and 0 quote a limit from a document only.

## Decisions against what happened

What the engineer was told, by how long each engine actually had left (NASA answer key, 200 active engines). Of the 45 engines that fail within 15 days, **100%** were sent for removal, swap or repair and 0 told to keep flying. Of the 102 engines with more than 40 days left, 1 pulled early and 41 sent for an inspection.

| True remaining life | Engines | Remove, swap or repair | Inspect | Keep flying |
| --- | --- | --- | --- | --- |
| fails within 15 days | 45 | 45 | 0 | 0 |
| 15 to 40 days | 53 | 14 | 38 | 1 |
| more than 40 days | 102 | 1 | 41 | 60 |

## Critic and escalation

- Critic rounds per run: 1: 163, 2: 37.
- Escalations: sb_adoption 105, rul_divergence 54, document_mandate 48, rul_below_lead_time 27, ambiguous_fault 1.
- Planner follow-ups after round 1, only where the findings call for them: 54 of 200 runs (inspection_history 54, backtest 54); follow-ups that changed the plan: inspection brought forward to the first free slot 3.
- Gaps routed by the planner to the agent that fixes them: driver_signature -> maint_planner 37.

## Seeded faulty briefs

311 faulty briefs built from 30 finished briefs (the 4 golden cases plus a random sample). The deterministic gates (rule checks, then the critic's deterministic checks; no LLM) caught **311 of 311 (100%)**. Clean briefs flagged: 0.

| Fault | Seeded | Caught |
| --- | --- | --- |
| uncited_claim | 30 | 30 |
| evidence_deleted | 30 | 30 |
| rul_above_cap | 30 | 30 |
| rul_outside_interval | 17 | 17 |
| rul_inflated | 12 | 12 |
| disagreement_averaged | 6 | 6 |
| wrong_asset | 30 | 30 |
| fabricated_part | 30 | 30 |
| stock_inflated | 3 | 3 |
| slot_not_free | 30 | 30 |
| deadline_past_rul | 30 | 30 |
| mandate_dropped | 8 | 8 |
| mandate_uncited | 8 | 8 |
| drivers_as_signature | 5 | 5 |
| similar_cases_dropped | 21 | 21 |
| claim_number_altered | 21 | 21 |

## Implausible input data

Clean fleet: 0 of 200 runs raised a data-quality issue.

116 runs on corrupted inputs (10 engines, the 4 golden cases plus a random sample), full graph. A serious fault must end in a `data_quality` escalation with confidence low; a minor one must be flagged in the brief without escalation. Graded as expected: **116 of 116 (100%)**.

| Fault | Expected | Seeded | Graded as expected |
| --- | --- | --- | --- |
| spike_t30 | serious | 10 | 10 |
| p30_in_kpa | serious | 10 | 10 |
| frozen_ps30 | serious | 10 | 10 |
| missing_phi | serious | 10 | 10 |
| duplicated_cycles | serious | 10 | 10 |
| cycles_out_of_order | serious | 10 | 10 |
| telemetry_truncated | serious | 10 | 10 |
| removal_on_active_engine | serious | 10 | 10 |
| negative_stock | serious | 6 | 6 |
| cycle_gap | minor | 10 | 10 |
| t2_out_of_range | minor | 10 | 10 |
| work_order_after_last_cycle | minor | 10 | 10 |
