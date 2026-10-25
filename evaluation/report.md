# Evaluation report (sample mode, synthetic fleet)

Generated 2026-10-25 19:42 UTC by `uv run task eval`. 200 active engines through the full agent graph on the committed snapshot; scored against the NASA C-MAPSS answer key, which the agents never read.

## Run completion

- Statuses: needs_review 200; crashes 0.
- Time per run: median 0.36 s, 95th percentile 0.42 s, max 0.47 s (limit 120 s).

## Remaining life

- RUL A (LightGBM): RMSE 12.13, MAE 8.6; 10th-90th interval covers 73% of true values (nominal 80%).
- RUL B (health index): RMSE 22.31, MAE 15.72; RMSE 7.55 when true RUL is 25 cycles or less.
- Planning bound (min of A 10th percentile and B lower) is at or below the true RUL for 90.0% of engines. 20 engines overshoot (mean 8.7 cycles); flagged for inspection or low confidence among them: 1. Near failure it matters most: of the 45 engines within 30 cycles (15 days) of failure, 1 overshoot.
- Methods disagree (> 20 cycles) on 54 engines (27%); RUL A error there is 11.61 vs 7.48 elsewhere, so the flag points at genuinely uncertain engines.

## Fault mode

- FD003 accuracy against the proxy label: 97.0% of 100 engines; 100.0% of the 41 engines within 60 cycles of failure (early in life the signature has not separated yet).

## Evidence

- 2907 claims; 100.0% cite evidence IDs that resolve.
- 1898 claims state a quantity (identifiers such as document sections excluded); 100.0% cite model, query, rule or case-memory evidence computed in code, and 0 quote a limit from a document only.

## Critic and escalation

- Critic rounds per run: 1: 163, 2: 37.
- Escalations: rul_divergence 54, document_mandate 48, rul_below_lead_time 27, ambiguous_fault 1.

## Seeded faulty briefs

290 faulty briefs built from 30 finished briefs (the 4 golden cases plus a random sample). The deterministic gates (rule checks, then the critic's deterministic checks; no LLM) caught **290 of 290 (100%)**. Clean briefs flagged: 0.

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
