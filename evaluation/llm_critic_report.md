# LLM critic on its own (Nemotron through Nightshift)

Generated 2026-10-25 21:47 UTC by `uv run task eval-llm`. Model: nvidia/nemotron-3-ultra-550b-a55b.

The four golden briefs, each corrupted with every seeded fault that applies, go to the LLM critic with the deterministic gates bypassed. A fault counts as caught when the model raises a checkable challenge that code confirms against the evidence. In the product the deterministic gates run first; this measures what the model adds on its own.

- Answered: 41 of 42 faulty briefs (median 7.4 s).
- Caught by a challenge code confirmed: **3 of 41**.
- Flagged in free text (notes, not checkable): 34 of 41.
- Challenges raised: 150; refuted by code: 147.
- Clean briefs: 4; vetoed by a confirmed challenge: 0.

| Fault | Answered | Caught by a confirmed challenge |
| --- | --- | --- |
| uncited_claim | 4 | 0 |
| evidence_deleted | 4 | 1 |
| rul_above_cap | 4 | 0 |
| wrong_asset | 4 | 0 |
| fabricated_part | 4 | 1 |
| slot_not_free | 4 | 0 |
| deadline_past_rul | 4 | 1 |
| rul_outside_interval | 2 | 0 |
| rul_inflated | 1 | 0 |
| mandate_dropped | 1 | 0 |
| mandate_uncited | 1 | 0 |
| similar_cases_dropped | 3 | 0 |
| claim_number_altered | 3 | 0 |
| stock_inflated | 1 | 0 |
| drivers_as_signature | 1 | 0 |
