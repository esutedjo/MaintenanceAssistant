# Vendored Meterless engines

Pre-existing components, copied unchanged from [github.com/Meterless/Meterless](https://github.com/Meterless/Meterless) at commit `1742dec56a66e58eef9dda2d2b865823758ca8cd` (2026-10-19). Apache License 2.0; each engine folder keeps its `LICENSE`.

| Folder | Source path | What it is |
| --- | --- | --- |
| `hmem/src` | `engines/hmem/reference/src` | H-MEM reference implementation (tiered memory, hybrid retrieval, trust ledger, conflicts, sleep, dreaming) |
| `world-model/src` | `engines/world-model/reference/src` | World Model reference implementation (event-sourced entities, contexts, bitemporal relationships, facts, audit) |
| `conformance/hmem` | `engines/hmem/conformance` | Official H-MEM conformance runner and vectors |
| `conformance/world-model` | `engines/world-model/conformance` | Official World Model conformance runner |

Our code is in `../src`: the REST service, the turbofan domain mapping and the JSONL evidence logs. Run `npm run conformance` to check the engines this service loads.

To update: copy the same paths from a newer Meterless commit, update the commit above, then run `npm run conformance` and `npm test`.
