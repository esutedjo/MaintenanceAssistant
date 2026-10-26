# Research findings for Cleris

26 Oct 2026

This page collects the market and regulatory findings that point to new features or changes for Cleris. They come from a competitive and regulatory research pass on 25–26 Oct 2026 that reviewed more than 30 products (engine OEM health monitoring, airframer data platforms, M&E and MRO software, cross-industry copilots, startups), EASA and FAA material, PHM research and industry cost data. The report and research notes are kept outside this repository. Every external claim below links to the source the notes give.

How to read it:

- Each finding says what the sources say, what it implies for Cleris, and the status in Cleris today: **built**, **partly built** or **not built**. Status was checked against this repository on 26 Oct 2026, and the files that show it are named.
- **Vendor claim** marks a capability or figure stated by the vendor, its customer, or a press item repeating them. None was checked independently.
- Each source carries its date. "Undated" product pages were read in Oct 2026. Regulations are cited as the current text read in Oct 2026.
- "No product documents X" means the research found no public material on X. It does not prove the capability is absent.
- Cleris results are quoted from `evaluation/report.md` and `evaluation/llm_critic_report.md` (25 Oct 2026, sample mode, NASA C-MAPSS simulated engines, synthetic maintenance data).
- These are proposals, not commitments. Code freeze is 27 Oct 2026 12:00 UTC (`AGENTS.md` §1), so the code changes below are post-hackathon work unless the team decides otherwise.

## Summary

| # | Finding | Proposed change | Status |
| --- | --- | --- | --- |
| 1.1 | Return to service is signed by a certificated person | Record signer identity and certificate with the sign-off | partly built |
| 1.2 | Airworthiness directives are legal obligations | Keep mandates as hard constraints | built |
| 1.3 | Draft EASA guidance treats tools like Cleris as Level 1 decision support; AI rules are not binding yet | State the intended use; keep the engineer as the decision-maker | partly built |
| 2.1 | EASA Proposed Issue 03 sets expectations on explanation, uncertainty, LLM use, errors and audit | Close the gaps in the mapping table | partly built |
| 2.2 | Asymmetric conformal intervals penalise late maintenance | Weight RUL A's range against late removal; calibrate or drop RUL B's range | partly built |
| 2.3 | LLM-written engine reports cite sensors that are not in the evidence | Rule check on sensor and component names in claims | partly built |
| 3.1 | Overlay products already read AMOS, TRAX, IFS, Ramco and SAP | Source mapping as configuration; one adapter for a real M&E schema | partly built |
| 3.2 | Data quality is the main reported barrier to AI in MRO | Extend the plausibility gate per source system | partly built |
| 3.3 | Airlines control who receives their data; OEM-derived data is contested | Owner and licence per evidence record; cite OEM outputs, do not copy them | partly built |
| 4.1 | Others time removals with borescope images | Bring inspection results back into the brief | partly built |
| 4.2 | Others cluster repeat defects from logbooks | Repeat-defect evidence per engine | not built |
| 4.3 | AD/SB applicability agents are spreading in M&E suites | Extract applicability from real AD/SB text; compliance status per engine | partly built |
| 4.4 | OEMs send notification reports and alerts on the same engines | OEM notifications as cited evidence | not built |
| 4.5 | Life-limited parts drive planned removals | LLP remaining cycles as a limit and a planning input | not built |
| 5.1 | The tolerable false-alarm rate depends on what a false alarm costs | Cost-aware inspect and remove thresholds; evaluation at a realistic failure rate | not built |
| 5.2 | Spare-engine leases and shop turnaround dominate the cost of a removal | Price the swap, wait and expedite options in code | partly built |
| 5.3 | Reported engine AI results are in shop-visit and material planning | Fleet-level slot allocation and parts demand from fleet triage | partly built |
| 6.1 | "Clearance" and "release" are regulated words; no "not for operational use" statement | Change the wording; add a disclaimer | partly built |
| 6.2 | Several claims would not survive a check | Use the "say instead" wording | partly built |
| 6.3 | The current competitor line can be rebutted | Use the corrected line | partly built |
| 6.4 | OEM care contracts limit the airline engineer's decision | Name the target engines and users | not built |

## 1. Regulation and sign-off

### 1.1 Return to service is signed by a certificated person; the Approve record names no one

**Finding.** In the US, the maintenance record must carry the signature, certificate number and kind of certificate of the person approving the work, and that signature is the approval for return to service for the work performed. Staff of a domestic repair station who approve an article for return to service must hold a mechanic or repairman certificate under Part 65. An air carrier stays primarily responsible for the airworthiness of its aircraft, including engines, when it contracts maintenance out. In the EU, under Part-145.A.50, authorised certifying staff issue the certificate of release to service on behalf of the organisation. EASA's draft AI guidance names sign-off in maintenance as an existing way to limit operation-related errors.

**Sources.**

- [14 CFR 43.9](https://www.law.cornell.edu/cfr/text/14/43.9) (US rule, current text read Oct 2026)
- [14 CFR 145.157](https://www.law.cornell.edu/cfr/text/14/145.157) (US rule, current text read Oct 2026)
- [14 CFR 121.363](https://www.law.cornell.edu/cfr/text/14/121.363) (US rule, current text read Oct 2026)
- [EASA CRS policy](https://www.easa.europa.eu/sites/default/files/dfu/CRS%20policy%20171215.pdf) (17 Dec 2015). The research did not check the current consolidated Part-145.A.50 text.
- [EASA AI Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en), Sect. C.4.7 (3 Jun 2026)
- Precedent for authenticated sign-off in M&E software: Ramco Aviation Software 6.0 offers biometric and geo-fenced sign-offs ([AviationPros](https://www.aviationpros.com/engines-components/press-release/55138822/ramco-systems-unveils-aviation-software-60-leveraging-ai-for-smarter-aircraft-management), 10 Sep 2024; vendor claim).

**What it means for Cleris.** Approve is the engineer's decision on a brief. It is not a return to service, and the record should say so and say who decided:

- the signer's name and an authenticated user ID, not a typed name;
- for US use, the certificate number and kind of certificate; for EU use, the certifying staff authorisation reference when the signer holds one;
- what was approved: the run ID plus a hash or copy of the brief and evidence as shown;
- a record type that reads as a decision record and draft work order, so the release itself stays with certifying staff in the shop's own M&E system.

**Status: partly built (decision log, added 26 Oct 2026).** The sign-off form (`ui/app.py` `_sign_off`) now requires a typed "Signed by" name, with optional role and licence reference, and keeps Approve disabled until every escalation is ticked as read. `approve_case` and `reject_case` (`tools/writeback.py`) record the signer, the UTC time and the brief signed (case ID, run ID and a SHA-256 of the recommendation) as the engineer's entry in the decision log (`casefile/decision_log.py`), an append-only, hash-chained file per case under `.state/decision_logs/`. The draft work-order record carries `signed_by`, `signed_at`, the brief reference, the log and a note that it is not a certificate of release to service. Still missing: the name is typed, not authenticated, and is not checked against a licence or authorisation register; the live insert into `cmms.work_orders` keeps its columns and runs as the shared `app_rw` user, so the signer travels in the JSONL record and the log, not in the CMMS row.

### 1.2 Airworthiness directives are legal obligations

**Finding.** Under 14 CFR 39.7, anyone who operates a product that does not meet an applicable airworthiness directive is in violation. An applicable AD is a constraint on the plan, not an option to trade against cost or schedule.

**Sources.** [14 CFR 39.7](https://www.law.cornell.edu/cfr/text/14/39.7) (US rule, current text read Oct 2026).

**What it means for Cleris.** Keep the current design: code decides whether a mandate applies, the mandate is shown first, and no agent can remove it. Real applicability data is covered in 4.3.

**Status: built (on synthetic documents).** `assurance/escalation.py` raises `document_mandate`, listed first. The rule check `mandates_honoured` (`assurance/rules.py`) sends back a plan whose mandated task is missing, uncited or late. The seeded faults `mandate_dropped` (8 of 8) and `mandate_uncited` (8 of 8) are caught (`evaluation/report.md`). The only AD is synthetic (`asset_packs/turbofan_cmapss/docs/ad_2026_17_04.md`).

### 1.3 Draft EASA guidance treats tools like Cleris as Level 1 decision support; AI rules are not binding yet

**Finding.** EASA's Proposed Issue 03 concept paper covers maintenance organisation approval holders and CAMOs. At Level 1 the end user takes the decision with support from the AI, and for Levels 0, 1 and 2A EASA considers the existing responsibility scheme unaffected. Its CAMO example, a tool that outputs a predicted corrosion level and a time to inspect, is classed Level 1A/1B. AI tools used by approved organisations are to be assessed for whether they perform a safety-related function, with a risk-based filter for tools that need qualification planned for step 2 of RMT.0742. NPA 2025-07 (Nov 2025) proposes AI-trustworthiness rules for Level 1 and 2 data-driven machine learning. The second NPA, which would apply them to domain rules such as maintenance, was planned for 2026 and was not on EASA's AI publications page by Oct 2026. The FAA's AI roadmap (Version I, 23 Jul 2024) treats AI as a tool, not a person, so accountability stays with people. It mentions maintenance only descriptively (digital twins, predictive maintenance) and sets out seven principles; there is no FAA rule or advisory circular on AI in maintenance (corrected 26 Oct 2026 after reading the roadmap at source; see `docs/regulatory-requirements.md`).

**Sources.**

- [EASA AI Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en), Sects. B.4, B.5.10, C.1 and Annex E.4 (3 Jun 2026; non-binding, released for consultation); [landing page](https://www.easa.europa.eu/en/document-library/general-publications/easa-artificial-intelligence-concept-paper-proposed-issue-3)
- [EASA news on NPA 2025-07](https://www.easa.europa.eu/en/newsroom-and-events/news/easas-first-regulatory-proposal-artificial-intelligence-aviation-now-open) (10 Nov 2025)
- [EASA AI publications page](https://www.easa.europa.eu/en/publication-types/artificial-intelligence) (read Oct 2026)
- [FAA Roadmap for AI Safety Assurance, Version I](https://www.faa.gov/aircraft/air_cert/step/roadmap_for_AI_safety_assurance) (23 Jul 2024; read at source in the regulatory review); also reported by [Airways](https://www.airwaysmag.com/new-post/faa-roadmap-ai-safety-assurance) (19 Aug 2024)

**What it means for Cleris.** The research notes infer that a Cleris-type assistant used by a CAMO or Part-145 powerplant engineer most plausibly sits at Level 1B, human support in decision and action selection. That is an inference, not an EASA classification. It favours what Cleris already does (the engineer decides, the LLM stays off the decision path) and argues for three documentation changes: describe Cleris as decision support, state that no AI rule for maintenance is in force yet and that Cleris claims no compliance, and track the second NPA and the qualification filter.

**Status: partly built.** The engineer decides: nothing is written before Approve (`tools/writeback.py`; README "Assurance", step 4). Since 26 Oct 2026 the README states the intended use (advisory decision support, never the sole source of an airworthiness determination or interval change, never a release to service) and the certification status, and `docs/regulatory-requirements.md` maps the requirements. `AGENTS.md` and `CLAUDE.md` do not yet carry the statement.

## 2. Explainability and uncertainty

### 2.1 EASA Proposed Issue 03 mapped to Cleris

**Finding.** The concept paper names the maintenance engineer as an audience owed operational explainability. It lists the sources the AI-based system used for decision-making as part of an explanation, expects an indication of confidence based on measurement or quantified uncertainty, allows large off-the-shelf models such as LLMs only at the lowest assurance level, and sets objectives for error management, auditability and training on over-reliance. It also states that credit is not taken from the architecture when two independent AI/ML constituents are combined (see 6.2). Several objectives (ORG-xx) fall on the organisation that uses the tool; the tool can support them.

**Sources.** [EASA AI Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en) (3 Jun 2026), sections as listed in the table.

| EASA expectation (section) | What Cleris does today | Status | Gap or next step |
| --- | --- | --- | --- |
| Operational explainability for the maintenance engineer (C.4.3.1; EXP-02 to EXP-04) | Verdict, plan and "Why this plan and not another" with each rule, this engine's value and where the plan would change (`ui/brief.py` `decision_boundaries`); "In plain words" (`ui/help.py` `plain_summary`); Explain switch and glossary | partly built | EXP-04 asks that understandability, timeliness and validity of the explanation be verified. No engineer has evaluated the brief. |
| Sources used for the decision are part of the explanation (anticipated MOC EXP-04-2) | Every claim cites evidence IDs; 2907 claims, 100.0% resolve. Each evidence record names its source (`zetaris:...`, `snapshot:...`, `model:...`) and is flagged `degraded` when it comes from a fallback (`casefile/schemas.py`) | built | None for the prototype. |
| Confidence indication based on quantified uncertainty (B.2, C.2) | RUL A's 10th–90th range is calibrated: covers 80% of true values (nominal 80%), 73% before calibration. A divergence above 20 cycles lowers confidence | partly built | The high/medium/low label is set by rules (divergence, ambiguous fault, data quality, severity) in `agents/maint_planner.py`, not from the interval. RUL B's range covers 45% (`rul_report.json`) and is not calibrated, yet its lower bound feeds the planning bound. |
| Monitor that inputs stay inside the operational design domain (B.2, C.2) | Input plausibility gate against ranges and steps seen on the C-MAPSS training engines (`tools/data_quality.py`, `sensor_ranges.json`); 116 of 116 corrupted-input runs graded as expected | partly built | The domain is C-MAPSS-shaped. Nothing tracks in service whether outputs stay within the stated confidence, for example range coverage against later outcomes. |
| Large off-the-shelf models only at the lowest assurance level; safe incorporation (C.1; C.3, RU-03) | The LLM reads free-text questions only when the parser finds no engine, and code accepts its answer only if grounded (`agents/intake.py`). The critic changes the outcome only through challenges code confirms. No LLM produces a number; sample mode calls none | built | None for the prototype. |
| Re-verify when an off-the-shelf model version changes (RU-04) | The trace records the served model; `uv run task eval-llm` measures the critic alone | partly built | Nothing runs the critic evaluation when the served model changes. |
| Error management (C.4.7; EM-01 to EM-05) | Deterministic gates caught 311 of 311 seeded faulty briefs with 0 false alarms; escalation rules; data-quality gate; nothing is written before Approve; Approve stays disabled until every escalation is ticked as read | partly built | The gates find errors in the brief. Nothing checks that the engineer opened the evidence before approving. |
| Record data to explain behaviour after operation (CRA-02); auditable by internal and external parties, including authorities (ORG-06) | One trace per run in `logs/trace-<run_id>.jsonl`; brief JSON; a decision log per case (agent, gate, model and engineer entries), append-only and hash-chained, attached to the draft work-order record (`casefile/decision_log.py`) | partly built | `logs/` and `.state/` are local and gitignored. The sign-off stores a typed signer and a SHA-256 of the recommendation, not a copy of the full brief and evidence. No retention, backup or access design. |
| Continued risk assessment from in-service data and end-user feedback (ORG-03) | Approve, reject and per-case ratings move H-MEM trust; `wrong` tags a case for review | partly built | No aggregate view of reject and override rates, or of range coverage against later outcomes. |
| Channel for reporting AI vulnerabilities or bias (ORG-05) | Reject with a comment | not built | A way to flag a brief as wrong in a specific way, kept separate from case-memory trust. |
| Training on the risk of over-reliance and de-skilling (ORG-07) | The Help tab explains how to read the brief | not built | Training notes and in-UI prompts that ask the engineer to check named evidence before approving. EASA also notes that poor interaction or bad experience can lead users to switch a system off (C.4.3.2). |
| Level 1: the end user takes the decision; responsibility scheme unaffected (B.4) | Approve is required before any write | built | Pair with 1.1 so the record names who decided. |

**Status: partly built** (per row above).

### 2.2 Asymmetric conformal intervals that penalise late maintenance

**Finding.** A 2026 IJPHM paper applies asymmetric conformalised quantile regression to a gradient-boosting RUL model on C-MAPSS. It allocates the allowed miscoverage unequally so that optimistic predictions, and so late maintenance, are less likely. It reports 88–92% coverage at a 90% target across the four C-MAPSS subsets, with test RMSE 13.26–16.85, and argues that point predictions have limited value without dependable uncertainty. Earlier work showed conformal prediction gives intervals with near-nominal coverage on C-MAPSS for several model types. The evaluation protocols differ from Cleris's, so RMSE figures should not be ranked against each other.

**Sources.**

- Robinson, [Remaining Useful Life Estimation for Aircraft Engines with Risk-Aware Prediction Intervals via Conformalized Quantile Regression](https://papers.phmsociety.org/index.php/ijphm/article/view/4724), IJPHM Vol. 17 No. 1 (27 Apr 2026)
- Javanmardi and Hüllermeier, [Conformal Prediction Intervals for Remaining Useful Lifetime Estimation](https://arxiv.org/abs/2212.14612) (arXiv Dec 2022; IJPHM 2023)
- EASA expects a confidence indication based on quantified uncertainty: [Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en), Sects. B.2 and C.2 (3 Jun 2026)

**What it means for Cleris.** Cleris plans from the lower bound, so a miss below the range is the costly one. Next steps:

- in `models/calibrate_rul.py`, split the 20% miscoverage unequally, for example 5% below and 15% above instead of 10% each, and tie the split to the cost ratio from 5.1;
- report the miss rate on each side in the evaluation, next to the planning-bound safety figure;
- calibrate RUL B's lower bound the same way, or stop using it in the planning bound until it is calibrated.

**Status: partly built.** `models/calibrate_rul.py` already fits one margin per side on the 40 held-out engines (lower widened by 1.8 cycles, upper by 0; `rul_report.json`), but both sides use the same 10% miscoverage. Results: RUL A's range covers 80% of true values (nominal 80%; 73% before calibration; 0.795 in `rul_report.json`); the planning bound is at or below the true RUL for 92.0% of engines, and 1 of the 45 engines within 30 cycles (15 days) of failure overshoots (`evaluation/report.md`). RUL B is not fitted on 69 of 200 engines, and its range covers 45%.

### 2.3 LLM-written engine reports cite sensors that are not in the evidence

**Finding.** A 2026 study of LLM-written aero-engine diagnostic reports found hallucination rates of 0.152 without FMEA knowledge injection, 0.122 with it, and 0.054 for an offline template. The hallucinations came mainly from the LLM citing sensor channels outside the residual-analysis results. This comes from a search excerpt; the full paper was not read. EASA names emergent behaviour and unexpected outputs, including hallucination, as a challenge specific to generative AI.

**Sources.** [arXiv 2604.22777](https://arxiv.org/pdf/2604.22777) (2026; search excerpt); [EASA Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en), Sect. B.1 (3 Jun 2026).

**What it means for Cleris.** In Cleris the brief text is written by code, so the exposure is in the critic's notes and any future LLM-written text. Add a rule check that every sensor and component a claim names appears in its cited evidence, next to the number check, and a seeded fault for it.

**Status: partly built.** `numbers_supported` (`assurance/challenges.py`, `assurance/rules.py`) checks every number a claim states; `drivers_vs_fault` (`agents/critic.py`) stops SHAP drivers being passed off as the fault signature. No check covers sensor or component names in claim text.

## 3. Data integration and data rights

### 3.1 Overlay products already read the M&E systems shops run

**Finding.** Several products read existing M&E and ERP systems instead of replacing them:

- ePlane AI's AeroGenie turns questions into SQL over AMOS, TRAX, IFS, Ramco, SAP S/4HANA, Snowflake and others, shows the SQL for editing, and says it does not touch the data itself (vendor claim; waitlist, no named customers).
- Aerostrat's long-range planning works whatever maintenance ERP the customer runs (AAR, which bought it).
- Lufthansa Technik's AVIATAR repetitives tool integrates with AMOS.
- Trax and Rolls-Royce launched an interface between Trax eMRO and Blue Data Thread.
- Boeing's Dataliner keeps airline data in the airline's own environment and offers APIs; its TechOps AI Agent has not been described (vendor claim).
- Swiss-AS announced no AI feature for AMOS in its May–Oct 2026 news.

**Sources.**

- [ePlane AI, AeroGenie](https://www.eplaneai.com/blog/aerogenie-ask-your-aviation-data-anything) (11 Jun 2025; vendor claim)
- [AAR, Aerostrat acquisition](https://www.aarcorp.com/en/newsroom/press-releases/2025/aar-announces-acquisition-of-maintenance-planning-software-provider-aerostrat-expanding-capabilities-of-trax-subsidiary/) (12 Aug 2025; vendor claim)
- [DOM magazine, AVIATAR TRE](https://dommagazine.com/node/304194) (25 Feb 2025; vendor claim)
- [AAR, Trax and Rolls-Royce interface](https://www.aarcorp.com/en/newsroom/press-releases/2025/trax-and-rolls-royce-launch-interface-to-improve-engine-on-wing-operations/) (8 Apr 2025; vendor claim)
- [AviationPros, Boeing Dataliner](https://www.aviationpros.com/aircraft-maintenance-technology/aircraft-technology/press-release/55413479/boeing-announces-new-ai-powered-platform-for-aircraft-data-management-simplifying-maintenance) (22 Oct 2026; vendor claim)
- [Swiss-AS news](https://www.swiss-as.com/news-events/news) (items 5 May–9 Oct 2026)

**What it means for Cleris.** "Reads the shop's systems in place" is shared with these products, so the connector layer has to exist before the claim can be made for a real shop. Next steps:

- move table and column names from code into the asset pack's `data_sources` block, with a column mapping per source system;
- build one read-only adapter for a real M&E schema (work orders, findings, parts and stock, slots), registered in Zetaris; the deck roadmap names SAP PM, Maximo and AMOS write-back;
- show the SQL in the Evidence tab; live-mode evidence records already keep it.

**Status: partly built.** Built: the read-only gateway with a SELECT-only guard (`tools/zetaris.py`, `assurance/sql_guard.py`), the snapshot fallback, and the SQL text in live-mode evidence content (`tools/data_tools.py`). Not built: the manifest's `data_sources` block is only displayed (`ui/app.py`, asset pack view); the SQL in `tools/data_access.py` hard-codes `cmms_pg.*`, `telemetry_pg.cycles`, `s3_files.fmea` and `knowledge_pg.doc_chunks`; the sources are tables the team seeded with synthetic data; there is no connector to AMOS, TRAX, IFS, SAP PM or Maximo.

### 3.2 Data quality is the main reported barrier to AI in MRO

**Finding.** In McKinsey's MRO digital survey (fielded Sep 2023), 84% named data accessibility, availability, quality and completeness as a barrier to digital deployment, and 82% limited data compatibility (figures confirmed from search excerpts; the page timed out). In Oliver Wyman's 2026 MRO survey, 58% say their AI work is still experimental, and scaling is held back mainly by data quality and availability. Siemens's Asset Essentials copilot warns that missing manuals or incomplete history reduce the usefulness of its output (vendor documentation).

**Sources.**

- [McKinsey, AI wings for fleet fixes](https://www.mckinsey.com/featured-insights/charts/ai-wings-for-fleet-fixes) (survey fielded Sep 2023)
- [Oliver Wyman MRO Survey 2026](https://www.oliverwyman.com/our-expertise/insights/2026/apr/aviation-mro-labor-and-material-supply-chain-paradigm.html) (Apr 2026)
- [Siemens Asset Essentials help, Maintenance Copilot](https://help.assetmanagement.siemens.com/help/Content/Documentation/Maintenance/Asset%20Essentials/EnterpriseFeatures/AI-Driven%20Capabilities/Maintenance%20Copilot.htm) (undated; vendor documentation)

**What it means for Cleris.** Reading data in place does not clean it. Keep the plausibility gate at the point of use and extend it when real sources arrive: unit and code mapping checks per source system, missing-field rates per source, stale-feed detection, and a brief line that says which evidence is missing, not only which is wrong.

**Status: partly built.** The gate is built for C-MAPSS telemetry, CMMS history and stock (`tools/data_quality.py`): 0 of 200 clean runs raised a data-quality issue, and 116 of 116 corrupted-input runs were graded at the expected severity. Its limits come from the C-MAPSS training engines, and there are no per-source checks because there is only one source shape.

### 3.3 Airlines control who receives their data; OEM-derived data is contested

**Finding.** The IATA aircraft operational data principles, signed by Airbus, Embraer and Rolls-Royce, require the airline's consent to extract operational data and let the airline choose who receives it. The EU Data Act extends data access to owners, lessees and third parties they appoint, such as MROs. The US Chamber argues that OEM-derived data is proprietary information and intellectual property. MTU's engine-health service with Teledyne lets airlines choose which parameters are shared. Boeing's Dataliner keeps data in the airline's environment (vendor claim).

**Sources.**

- [IATA press release](https://www.iata.org/en/pressroom/2024-releases/2024-10-02-01/) (2 Oct 2024)
- [Bird & Bird, EU Data Act aviation flyer](https://www.twobirds.com/-/media/new-website-content/pdfs/trending-topics/eu-data-act/eu-data-act-flyer---aviation.pdf) (2026)
- [US Chamber, EU Data Act report](https://www.uschamber.com/assets/documents/US-Chamber-EU-Data-Act-Report.pdf) (undated)
- [MTU Maintenance and Teledyne Controls](https://www.stocktitan.net/news/TDY/mtu-maintenance-and-teledyne-controls-partner-to-deliver-enhanced-6bm5ymzd6zjr.html) (28 Oct 2025; vendor claim)
- [AviationPros, Boeing Dataliner](https://www.aviationpros.com/aircraft-maintenance-technology/aircraft-technology/press-release/55413479/boeing-announces-new-ai-powered-platform-for-aircraft-data-management-simplifying-maintenance) (22 Oct 2026; vendor claim)

**What it means for Cleris.** Assume Cleris may read raw sensor data and the shop's own records, but may not copy or redistribute OEM analytics. Add a data owner and licence field to each evidence record, and treat OEM outputs (4.4) as cite-only.

**Status: partly built.** Reads are read-only and nothing is copied into the app; each evidence record keeps its source and a `degraded` reason (`casefile/schemas.py`). There is no owner or licence field, and no OEM-derived source yet.

## 4. Evidence sources other products use

### 4.1 Borescope images are the evidence others use to time removals

**Finding.** GE's ABM.ai, its one removal-timing product, combines an analytics model with on-wing borescope images of HPT blades. GE says image review takes 2 hours instead of 1–2 days, with about 80% accuracy, and that a human always vets the decision (vendor claim). GE and Waygate released AI-assisted defect recognition for HPC borescope inspections, claiming recall up about 34% and false alerts down more than 13% against the previous model (vendor claim). GE's Blade Inspection Tool for LEAP is claimed to halve inspection time (vendor claim). Pratt & Whitney bought Aiir Innovations, whose software applies AI to borescope video, assists inspectors and adapts to their feedback (vendor claim). Rolls-Royce says a routine borescope inspection can take an aircraft out of service for 12 hours (vendor claim). EASA's draft guidance classes AI that detects damage in inspection images (X-ray, ultrasonic, thermography) and supports the repair decision for inspector validation as Level 1B.

**Sources.**

- [GE Aerospace 2025 Product and Technology Update](https://www.geaerospace.com/sites/default/files/2025-ge-aerospace-product-and-technology-update.pdf), p. 26 (Oct 2025; vendor claim)
- [GE Aerospace and Waygate Technologies](https://www.geaerospace.com/news/press-releases/ge-aerospace-waygate-technologies-deliver-new-ai-assisted-commercial-jet-engine) (28 Oct 2024; vendor claim)
- [GE Aerospace, Blade Inspection Tool](https://www.geaerospace.com/news/press-releases/ge-aerospace-deploys-ai-driven-inspection-tool-maximize-narrowbody-engine-time-wing) (13 Feb 2025; vendor claim)
- [RTX, Pratt & Whitney and Aiir Innovations](https://raytheon.mediaroom.com/2026-07-15-RTXs-Pratt-Whitney-advances-engine-inspections-with-AI-powered-technology) (15 Jul 2026; vendor claim)
- [Rolls-Royce, intelligent borescope](https://www.rolls-royce.com/media/our-stories/discover/2021/intelligentengine-harnessing-the-power-of-ai-to-deliver-more-intelligent-engine-inspections.aspx) (c. Feb 2021; vendor claim)
- [EASA Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en), Annex E.4 (3 Jun 2026)

**What it means for Cleris.** Cleris sends an engine for a borescope inspection when its two methods disagree (54 engines, 27%, in the evaluation), but the result never comes back into the brief. Add an inspection-result evidence kind (inspector's finding per stage with severity, and optionally the shop's AI grading and an image reference) and re-run the brief with it, so an "inspect" decision closes into keep or remove. Take the image analysis from the shop's existing borescope tool rather than building it.

**Status: partly built (26 Oct 2026).** Inspection findings now come back in: borescope, visual, oil-analysis, chip-detector and material-test findings are entered in the UI or the API, become evidence (`EV-INS-nn`), are classified by code against the manual's limit tables (synthetic `AMM-TF-R3 …-601` sections), and drive the plan (beyond limits: remove; at the limit: re-inspect; within limits on every suspected module: no removal), checked by the rule `inspections_acted_on` (`tools/inspections.py`, `agents/maint_planner.py`). Photos are stored with the finding but not analysed; there is no image AI.

### 4.2 Repeat defects and past fixes from maintenance logs

**Finding.** Lufthansa Technik's AVIATAR Technical Repetitives Examination groups free-text logbook defects by component across the fleet and a period, uses GenAI to pre-process the text, runs at more than 20 airlines and leaves the recommendation to the engineer. Collins's Ascentia Repeaters uses NLP to cluster chronic issues from maintenance logs; Collins says nearly 30% of unscheduled airline events are preceded by several maintenance reports (vendor claim). Airbus Skywise Fleet Performance+ uses NLP to find repetitive faults, link documentation references and estimate a most probable cause (vendor claim). Veryon AIRE shows fix rates and defect history drawn from more than 80 million pooled maintenance events (vendor claim). Ramco's deal with the engine shop Pem-Air includes corrective-action suggestions based on prior resolutions (vendor claim).

**Sources.**

- [DOM magazine, AVIATAR TRE](https://dommagazine.com/node/304194) (25 Feb 2025; vendor claim)
- [RTX, Ascentia Repeaters](https://www.rtx.com/news/news-center/2025/04/09/rtxs-collins-aerospace-launches-ascentia-repeaters-with-republic-airways) (9 Apr 2025; vendor claim)
- [Airbus, Skywise Fleet Performance+](https://aircraft.airbus.com/en/services/enhance/skywise-digital-solutions/skywise-fleet-performance) (undated; vendor claim)
- [Veryon AIRE](https://veryon.com/press-media/veryon-aire-redefines-aviation-maintenance-with-ai-powered-intelligence-across-the-entire-fleet) (7 Oct 2025, updated 22 Jul 2026; vendor claim)
- [Asian Aviation, Ramco at Pem-Air](https://asianaviation.com/posts/pem-air-taps-ramco-systems-for-engine-mro-software) (24 Aug 2026; vendor claim)

**What it means for Cleris.** Add repeat-defect evidence per engine: count and group this engine's tech-log and work-order entries by component and symptom over a window, computed in code and citing the entries. An LLM may normalise the free text, but the counts and the grouping rule stay in code. Fix outcomes of past cases could later feed H-MEM trust.

**Status: not built.** The Data agent summarises this engine's work orders and inspection findings (`tools/data_tools.py`, history evidence), and H-MEM returns similar past removals by failure mode, ranked by relevance times trust. Nothing reads a tech log or groups free-text defects.

### 4.3 AD and SB applicability agents are spreading in M&E suites

**Finding.** IFS describes an application that turns ADs and service bulletins into compliance job cards and claims processing more than 70% faster; this rests on one secondary source, published by ePlane AI, which is itself a vendor in this market. Ramco's Pem-Air deal includes a Service Bulletin Agent that extracts parameters from unstructured SBs and ADs for engineering orders. Fleetcraft's Trace reads maintenance and regulatory documents for AD applicability. All are vendor claims. Under 14 CFR 39.7 an applicable AD must be met (1.2).

**Sources.**

- [ePlane AI, IFS at MRO Americas](https://www.eplaneai.com/news/ifs-introduces-industrial-ai-for-aviation-technicians) (23 Apr 2026; vendor claim via a secondary source)
- [Asian Aviation, Ramco at Pem-Air](https://asianaviation.com/posts/pem-air-taps-ramco-systems-for-engine-mro-software) (24 Aug 2026; vendor claim)
- [Fleetcraft](https://fleetcraft.com/fleetcraft-raises-3-2m-to-boost-aircraft-maintenance-efficiency-with-ai/) (29 Jan 2026; vendor claim)
- [14 CFR 39.7](https://www.law.cornell.edu/cfr/text/14/39.7) (US rule, current text read Oct 2026)

**What it means for Cleris.** Real ADs and SBs state applicability by engine model, serial range and configuration, and the records state whether each has been complied with. Next steps: extract applicability and the required action from AD and SB text into Cleris's existing mandate structure, with a human check before an extracted mandate is used; add compliance status per engine from the records; keep the decision on whether a mandate applies in code, with the existing rule check.

**Status: partly built.** The Knowledge agent keeps revisions in effect for the fleet variant and components, orders them by precedence, and decides mandate conditions in code (`agents/knowledge.py`, `tools/knowledge.py`). The five documents are synthetic and their applicability and mandate conditions are hand-written metadata (for example `asset_packs/turbofan_cmapss/docs/ad_2026_17_04.md`). There is no serial-number applicability and no compliance status per engine. `AGENTS.md` lists the Federal Register API for ADs as roadmap only and cut from the hackathon scope.

### 4.4 OEMs send notification reports and alerts on the same engines

**Finding.** GE says it continuously monitors 44K+ commercial engines (the same page also says 30K ESNs monitored) and issues 18K+ Customer Notification Reports a year; standard diagnostics are free under a General Terms Agreement (vendor claim). Rolls-Royce EHM recommends specific Fault Isolation Manual or Maintenance Manual tasks (vendor claim). Pratt & Whitney's EngineWise ADEM gives automated alerts, fault isolation and troubleshooting recommendations (vendor claim). Each covers only that maker's engines.

**Sources.**

- [GE Aerospace, Collaborative Insight](https://www.geaerospace.com/collaborative-insight) (undated; vendor claim)
- [Rolls-Royce, Engine Health Monitoring](https://www.rolls-royce.com/products-and-services/civil-aerospace/services/ehm.aspx) (undated; vendor claim)
- [Pratt & Whitney, EngineWise Intelligence](https://www.rtx.com/en/prattwhitney/services/enginewise/intelligence) (undated; vendor claim)
- Data rights: see 3.3.

**What it means for Cleris.** An engineer already receives OEM alerts on the engine. A brief that ignores an open OEM notification on the same module is incomplete. Add an OEM-notification evidence kind, read from wherever the shop stores the reports, shown and cited in the brief, with a rule that an open notification on the affected module must be listed. Per 3.3, cite these outputs; do not copy or redistribute them.

**Status: not built.**

### 4.5 Life-limited parts drive planned removals

**Finding.** GE lists life-limited parts, ABM, Customer Notification Reports and engine monitoring as drivers of planned removals, and borescope findings, foreign object damage, leaks and premature distress as drivers of unplanned ones (vendor material). Aircraft Commerce noted that life left on LLPs at a shop visit is usually scrapped, and that the lowest cost per flight hour comes when the heavy shop visit coincides with full LLP use (older, 2004). A sponsored 2026 article says that when an LLP is the binding limit, the part with the fewest cycles left often drives removal timing.

**Sources.**

- [GE Aerospace 2025 Product and Technology Update](https://www.geaerospace.com/sites/default/files/2025-ge-aerospace-product-and-technology-update.pdf), p. 17 (Oct 2025; vendor claim)
- [Aircraft Commerce Issue 34](https://aircraft-commerce.com/wp-content/uploads/aircraft-commerce-docs/Maintenance/2004/ISSUE%2034-MTCE.pdf) (2004, older)
- [AviTrader, Effective engine LLP management](https://avitrader.com/2026/04/23/effective-engine-llp-management/) (23 Apr 2026; sponsored)

**What it means for Cleris.** Today the deadline is the earlier of the RUL lower bound and any mandate deadline. Add LLP remaining cycles as a hard limit on the deadline, and as a planning input so a health-driven removal can be combined with LLP replacement when stub life is short.

**Status: not built.** No LLP data exists in the snapshot, the CMMS seed or the asset pack.

## 5. Decision economics and fleet planning

### 5.1 The tolerable false-alarm rate depends on what a false alarm costs

**Finding.**

- Koops (Bauhaus Luftfahrt, PHME 2018) models predicting HPT-blade-driven unscheduled removals of widebody engines. When a false alarm needs only a borescope inspection, the best operating point accepts a false-positive rate of 0.73 at a true-positive rate of 1.00. When a false alarm forces a removal, the best point is a false-positive rate of 0.15–0.21 at a true-positive rate of 0.84–0.93, and in four of the six cases modelled a false-positive rate above about 0.6 costs more than no prediction. Net benefit per engine over one removal interval is $92–93k when false alarms need a borescope, against $12–47k when they force a removal (2012 cost basis). For a rare failure mode, almost all positive predictions are false.
- Safran engineers give a typical airline requirement that the probability of no degradation given an alert be below 5%, to limit no-fault-found removals.
- FAA cost estimates in AD 2022-17-12 put a LEAP-1A HPT stage-1 borescope inspection at 4 work-hours, $340 per engine, against $1,000,950 for an on-condition blade replacement (2022).
- easyJet sets predictive alert thresholds above shop-test fault thresholds to reduce no-fault-found removals (customer claim, 2022).

**Sources.**

- Koops, [Business case analysis of predictive maintenance](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf) (PHME 2018)
- Massé et al., [PHME 2014](https://papers.phmsociety.org/index.php/phme/article/download/1542/510) (2014)
- [Federal Register 87 FR 53651, AD 2022-17-12](https://www.govinfo.gov/content/pkg/FR-2022-09-01/pdf/2022-18923.pdf) (1 Sep 2022)
- [Aviation Week, easyJet predictive maintenance](https://aviationweek.com/mro/easyjet-reaps-big-gains-early-predictive-maintenance-efforts) (1 Nov 2022; customer claim)
- Ground time of a borescope: [Rolls-Royce](https://www.rolls-royce.com/media/our-stories/discover/2021/intelligentengine-harnessing-the-power-of-ai-to-deliver-more-intelligent-engine-inspections.aspx) (c. Feb 2021; vendor claim)

**Cleris today** (`evaluation/report.md`):

| True remaining life | Engines | Remove, swap or repair | Inspect | Keep flying |
| --- | --- | --- | --- | --- |
| fails within 15 days | 45 | 45 | 0 | 0 |
| 15 to 40 days | 53 | 14 | 38 | 1 |
| more than 40 days | 102 | 1 | 41 | 60 |

**What it means for Cleris.** The research notes read this as consistent with the cost-asymmetric optimum: 41 of 102 inspection calls on engines with more than 40 days left (about 40%) sits inside the range Koops finds tolerable when a false alarm costs a borescope, and 1 of 102 early pulls sits below the removal-level optimum. That is an inference on simulated engines at the C-MAPSS test-set mix, and it reverses if an inspection call often turns into a removal or a disrupted schedule. Next steps:

- put cost inputs in the asset pack: an inspection (labour and ground time), a removal or lease-engine swap, and a missed failure;
- choose the divergence threshold and the severity bands by expected cost on the held-out engines, and show the expected cost of each option in "Why this plan and not another";
- add an evaluation at a realistic failure rate (Koops uses 0.026 unscheduled removals per 1,000 flight hours for a mature widebody engine) and report the precision of inspect and remove calls, since operators judge alerts by precision.

**Status: not built.** `rul_divergence_cycles: 20`, `severe_rul_cycles: 25` and `moderate_rul_cycles: 80` in `asset_packs/turbofan_cmapss/manifest.yaml` are fixed values; the divergence threshold was chosen against RUL A's error, not cost. There are no cost inputs. The parts evidence carries `unit_cost_usd` (`tools/data_tools.py`), but no decision uses it. The evaluation reports counts at the C-MAPSS test-set mix.

### 5.2 Spare-engine leases and shop turnaround dominate the cost of a removal

**Finding.** IATA and Oliver Wyman put excess engine-leasing costs at $2.6bn in 2025. Engine shop visits have stretched from the standard 30–60 days to more than 300 days in some cases. In their sensitivity table (60-day baseline), industry engine-leasing cost rises from $0.9bn at 70 days of turnaround to $2.6bn at 90 days and $5.0bn at 120 days, roughly $0.8–0.9bn per extra 10 days. Completed short-term LEAP and PW1100G leases passed $6,500 a day in the year to Aug 2026, against about $5,000 in 2022–23 (IBA via Reuters, reported by Airways). An IBA appraisal gives $6,000–7,000 a day for a short-term LEAP-1B lease and $130,000–150,000 a month long term (Jul 2025 values).

**Sources.**

- [IATA and Oliver Wyman, Reviving the Commercial Aircraft Supply Chain](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/reviving-the-commercial-aircraft-supply-chain.pdf) (Oct 2025)
- [Airways, airline engine bills](https://www.airwaysmag.com/new-post/airline-engine-bills-rise-groundings-ease) (16 Aug 2026)
- [ISTAT, IBA appraisal of the CFM LEAP-1B](https://www.istat.org/ISTAT-Online/ISTAT-Online/Aircraft/ArtMID/1205/ArticleID/1639/Appraisal-CFM-LEAP-1B) (30 Sep 2025)

**What it means for Cleris.** The lease-engine swap Cleris proposes has a price, and so does waiting for parts and a slot. Add the shop's lease rate, expected shop turnaround and part costs as evidence; compute the cost of each feasible option (swap now, wait, expedite) in code; show them next to the plan. Keep the safety rule (RUL bound against lead time plus slot) as the gate and use cost only to choose among safe options. Until a cost model is measured, size the problem in the pitch and make no value claim for Cleris (6.2).

**Status: partly built.** The planner proposes a lease-engine swap when the RUL lower bound is shorter than parts lead time plus days to the first free slot (`agents/maint_planner.py`; escalation `rul_below_lead_time` on 27 engines in the evaluation). There is no lease rate, turnaround or AOG cost in the data, and `unit_cost_usd` is unused.

### 5.3 Reported engine AI results are in shop-visit and material planning

**Finding.**

- Rolls-Royce's Optimiser splits shop-visit planning (close to 1,500 shop visits in 2025) between an Engine Agent, a Workscope Agent, Facility Agents and a Supplier Agent under an orchestrator. It serves Rolls-Royce's own planners, is still in development, needed more than 40 business systems moved into Azure, and its benefit is described only as materially reduced effort (vendor claim, via Microsoft).
- Delta TechOps' APEX raised material-demand forecast accuracy from 60% to over 90% for shop-visit material planning; it is not described as supporting on-wing decisions (Delta-reported result).
- GE's AI-Material Assistant forecasts parts before a shop visit and is credited with about a 5-day average turnaround reduction (vendor claim).
- Aerogility uses multi-agent simulation for engine shop-visit forecasting and has done it for easyJet since Dec 2018 (older).

**Sources.**

- [Microsoft UK Stories, Rolls-Royce Optimiser](https://ukstories.microsoft.com/features/rolls-royce-using-ai-agents-to-help-keep-more-aircraft-flying-for-longer/) (21 Oct 2026; vendor claim)
- [Delta TechOps, APEX](https://deltatechops.com/awn-recognizes-innovative-transformative-engine-maintenance-operation-at-delta-techops/) (12 Apr 2024; Delta-reported)
- [GE Aerospace 2025 Product and Technology Update](https://www.geaerospace.com/sites/default/files/2025-ge-aerospace-product-and-technology-update.pdf), p. 37 (Oct 2025; vendor claim)
- [HMG Aerospace, easyJet and Aerogility](https://www.hmgaerospace.com/news/lara/easyjet-upgrades-engine-forecasting-capabilities-with-aerogility/) (undated article; older)

**What it means for Cleris.** A fleet-level extension, kept separate from the single-engine brief: allocate hangar slots across the engines fleet triage flags (today each brief takes the first free slot on its own), and sum part demand from the planned tasks over a horizon against stock and lead times, computed in code. Optimiser and Aerogility mean Cleris should not be pitched as a shop-visit planner or as new in multi-agent engine planning (6.2).

**Status: partly built.** Fleet triage (`tools/triage.py`, `uv run task fleet-triage`) runs the full graph on all 200 active engines and ranks them by decision and deadline. Slots are not allocated across engines, and there is no parts-demand forecast. The deck roadmap lists fleet-wide scheduling across hangar slots as future work.

## 6. Positioning and wording

### 6.1 "Clearance" and "release" are regulated words; there is no "not for operational use" statement

**Finding.** A return to service is a certificated person's signature (14 CFR 43.9), and in the EU certifying staff issue the certificate of release to service (Part-145.A.50; see 1.1). The FAA roadmap asks that AI be treated as a tool and not personified. Cleris's product line and handoff notes use the regulated words for what the tool does, and neither the repository nor the pitch carries a certification statement or a "not for operational use" disclaimer.

**Sources.** [14 CFR 43.9](https://www.law.cornell.edu/cfr/text/14/43.9) (current text read Oct 2026); [EASA CRS policy](https://www.easa.europa.eu/sites/default/files/dfu/CRS%20policy%20171215.pdf) (17 Dec 2015); [Airways, FAA AI roadmap](https://www.airwaysmag.com/new-post/faa-roadmap-ai-safety-assurance) (19 Aug 2024).

**What it means for Cleris.** Keep the name Cleris. Wording is a team decision (`CLAUDE.md` fixes the product line), so these are suggestions:

| Where (today) | Current wording | Suggested |
| --- | --- | --- |
| `README.md` line 3, `AGENTS.md` §1, `pyproject.toml`, UI header (`ui/app.py`) | "the MRO clearance assistant" | Keep the line if the team wants it, but define it at first use: "Cleris prepares the stay-or-remove decision and its evidence; certifying staff issue the release in the shop's own system." |
| `CLAUDE.md` "What Cleris is" | "Cleris writes that clearance." | "Cleris prepares the evidence; the engineer decides and signs." |
| `CLAUDE.md` "What Cleris is" | "Say 'maintenance release' ... the first time the name meets a general audience" | Say "the on-wing decision (keep, inspect or remove)" and leave "release" to the signed act. |
| Deck slide 3 notes, as quoted in `CLAUDE.md` "Discussed and not done" #2 | "with the release the engineer signs" | "with the decision the engineer approves; the release is signed in the shop's system" |
| Approve button and help text (`ui/app.py`, `ui/help.py`) | "Approve and draft work order" | Already accurate; keep "drafts the work order the engineer approves". |

Suggested disclaimer for the top of the README, the UI caption under the header and the API output: "Hackathon prototype. Runs on NASA C-MAPSS simulated telemetry and synthetic maintenance, spares and document data. Not certified or approved by any aviation authority and not for operational use. It does not issue a return to service or a certificate of release to service."

**Status: partly built (26 Oct 2026).** The README opens with the disclaimer above, and the sign-off now says that approval drafts a work order for the organisation's own release process and is not a certificate of release to service (`ui/app.py`, `casefile/decision_log.py`). The product line "the MRO clearance assistant" and the `CLAUDE.md` wording are unchanged, as a team decision.

### 6.2 Claims the team must not make

**Finding.** The research checked the pitch claims against what competitors, regulators and Cleris's own evaluation show. The table is from the research report; the last column says what the repository's docs say today.

| Do not say | Why | Say instead | Repo docs today |
| --- | --- | --- | --- |
| "First AI maintenance assistant" or "first multi-agent engine planner" | Maximo Assistant, Senseye Copilot and Atlas AI already ship. Rolls-Royce Optimiser runs engine, workscope, facility and supplier agents (Oct 2026). Aerogility has used software agents for easyJet engine forecasts since 2018 | "A decision brief whose every number is code-checked" | `AGENTS.md` §1 forbids the claim |
| "The only tool that works on your existing systems" or "no integration needed" | AeroGenie writes SQL over AMOS, TRAX, IFS, Ramco and SAP without touching data; Aerostrat works with any ERP; Dataliner keeps data in the airline's environment. Cleris needs Zetaris registration and hard-coded table mapping, and has no AMOS, SAP or Maximo connector (3.1) | "Reads sources in place through one read-only gateway; nothing is copied into the app" | `CLAUDE.md` says "It sits on the systems the shop already has"; `AGENTS.md` §1 says "no data migration", which is true only in the narrow sense |
| "Competitors can't explain their advice" or "don't use your data" | Senseye and Cognite connect to existing sources; C3 markets traceable answers; Rolls-Royce EHM links advice to manual tasks. Absence from public documents is not proof of absence | "None publicly documents per-claim, code-checked evidence" | `CLAUDE.md` "Discussed and not done" #1 already warns against inventing gaps |
| "Accurate on real engines", "97% diagnosis accuracy" or "beats state-of-the-art RMSE" | The data is NASA C-MAPSS simulation with 2 fleets and 2 fault modes; the 97% is agreement with a proxy label from one sensor; no benchmark comparison was run | "Measured on held-out simulated engines" | README labels the data simulated and the 97% as agreement with the proxy label |
| "Certified", "EASA/FAA compliant", or "Cleris writes the clearance/release" | No certification and no regulatory statement; a return to service is a certificated person's signature (14 CFR 43.9; Part-145.A.50); the record has no signer (1.1); EASA AI rules are not binding (1.3) | "Prepares the evidence; the engineer decides and signs" and "drafts the work order the engineer approves" | `CLAUDE.md` says "Cleris writes that clearance" (6.1) |
| "Saves hours", "prevents AOG" or "saves $X" | No time or cost has been measured, and the hackathon judges discount unsupported claims of value | Size the problem: "$2.6bn excess engine leasing in 2025; short-term leases above $6,500 a day" (5.2) | No value claim in the README or `AGENTS.md` |
| "The LLM critic makes it safe" | On its own it caught 3 of 41 faulty briefs through a challenge code confirmed, and code refuted 147 of its 150 challenges | "Code decides; the critic raises challenges that code checks" | README states the measured result |
| "Two independent methods give redundancy", or any safety credit for them | EASA does not accept credit from the architecture for two independent AI/ML constituents; RUL B is not fitted on 69 of 200 engines | "Disagreement is a measured warning: RUL A's error is 11.61 cycles on flagged engines against 7.48 elsewhere" | `AGENTS.md` §2 and `ui/help.py` describe two independent methods as a design, with no safety-credit claim |
| "Production-ready", "live fleet data" or "works for any machine" | Built 22–27 Oct 2026; live mode runs on a Hobby-tier Zetaris cluster with 15–170 s stalls; a second asset type is not built | "Hackathon prototype for one engine type, with a demo-grade live mode" | `AGENTS.md` and `CLAUDE.md` state the limits |
| "Independently validated: 311 of 311" | The same team wrote the faults and the gates; three fault types have six or fewer samples | "Catches the 16 failure classes it was built to catch" | README gives 311 of 311 with the earlier misses disclosed |

**Sources.**

- [IBM Newsroom, Maximo Assistant](https://newsroom.ibm.com/blog-enhanced-maximo-streamlines-workforce-efficiency,-investment-planning,-and-facilities-management-introduces-gen-ai-assistant) (25 Jun 2025; vendor claim)
- [ETMM, Siemens Senseye Maintenance Copilot](https://www.etmm-online.com/siemens-industrial-copilot-ai-powered-maintenance-solution-a-76790d2aebb90810d857abf89c94c051/) (31 Mar 2025; vendor claim)
- [Cognite, Atlas AI release](https://www.cognite.com/en/resources/blog/cognite-atlas-ai-september-product-release-accelerating-agentic-ai-at-scale) (10 Sep 2025; vendor claim)
- [Microsoft UK Stories, Rolls-Royce Optimiser](https://ukstories.microsoft.com/features/rolls-royce-using-ai-agents-to-help-keep-more-aircraft-flying-for-longer/) (21 Oct 2026; vendor claim)
- [HMG Aerospace, easyJet and Aerogility](https://www.hmgaerospace.com/news/lara/easyjet-upgrades-engine-forecasting-capabilities-with-aerogility/) (undated article; older)
- [ePlane AI, AeroGenie](https://www.eplaneai.com/blog/aerogenie-ask-your-aviation-data-anything) (11 Jun 2025; vendor claim); [AAR, Aerostrat](https://www.aarcorp.com/en/newsroom/press-releases/2025/aar-announces-acquisition-of-maintenance-planning-software-provider-aerostrat-expanding-capabilities-of-trax-subsidiary/) (12 Aug 2025; vendor claim); [AviationPros, Boeing Dataliner](https://www.aviationpros.com/aircraft-maintenance-technology/aircraft-technology/press-release/55413479/boeing-announces-new-ai-powered-platform-for-aircraft-data-management-simplifying-maintenance) (22 Oct 2026; vendor claim)
- [Siemens, Senseye platform overview](https://www.siemens.com/global/en/products/services/digital-enterprise-services/analytics-artificial-intelligence-services/senseye-predictive-maintenance/senseye-cloud-application/platform-overview.html) (undated; vendor claim); [Cognite extractors](https://www.cognite.com/en/product/extractors) (undated; vendor claim); [C3 AI, generative AI](https://c3.ai/why-c3-generative-ai/) (undated; vendor claim); [Rolls-Royce EHM](https://www.rolls-royce.com/products-and-services/civil-aerospace/services/ehm.aspx) (undated; vendor claim)
- [EASA Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en), Sect. C.1 notes on independence (3 Jun 2026); [14 CFR 43.9](https://www.law.cornell.edu/cfr/text/14/43.9); [EASA CRS policy](https://www.easa.europa.eu/sites/default/files/dfu/CRS%20policy%20171215.pdf) (17 Dec 2015)
- [IATA and Oliver Wyman](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/reviving-the-commercial-aircraft-supply-chain.pdf) (Oct 2025); [Airways](https://www.airwaysmag.com/new-post/airline-engine-bills-rise-groundings-ease) (16 Aug 2026)
- Cleris figures: `evaluation/report.md`, `evaluation/llm_critic_report.md`, `models/artifacts/turbofan_cmapss/rul_report.json`, `models/artifacts/turbofan_cmapss/fault_mode_report.json`

**Status: partly built.** `AGENTS.md` already forbids the "first" claim and the README reports measured results with their limits. `CLAUDE.md` still carries "Cleris writes that clearance" and "It sits on the systems the shop already has".

### 6.3 The current competitor line can be rebutted

**Finding.** The current line is: "Senseye, Maximo Assistant and Cognite Atlas advise from inside their own platforms; Cleris sits on the systems the shop already has" (`CLAUDE.md` "Discussed and not done" #1, deck slide 2 notes and narration). Siemens says Senseye can use existing data sources without new hardware. Cognite's extractors only read source systems, and Cognite announced zero-copy sharing with Snowflake. For a shop that runs Maximo, Maximo is the existing system. None of the three has a named airline customer for these AI features. What holds is that Senseye and Cognite bring data into their own cloud or knowledge graph before they advise.

**Sources.**

- [Siemens, Senseye platform overview](https://www.siemens.com/global/en/products/services/digital-enterprise-services/analytics-artificial-intelligence-services/senseye-predictive-maintenance/senseye-cloud-application/platform-overview.html) (undated; vendor claim)
- [Cognite extractors](https://www.cognite.com/en/product/extractors) (undated; vendor claim); [Business Wire, Cognite and Snowflake](https://www.businesswire.com/news/home/20251010897497/en) (Oct 2025; vendor claim)
- [IBM Docs, Maximo AI Service](https://www.ibm.com/docs/SSRHPA_cd/appsuite/admin/c_ctr_ai_integrattion_manage.html) (undated; vendor documentation)

**What it means for Cleris.** Use the corrected lines from the research report:

- Narration: "Maximo Assistant, Senseye and Cognite Atlas answer from data held in, or copied into, their own platforms, and engine makers advise only on their own engines. Cleris is built to query the shop's existing systems where the data lives, read-only, and its code, not the model, checks every number before an engineer signs off."
- Slide: "Copilots answer from data in their own platforms. Cleris reads the shop's systems in place, and code checks every number before an engineer signs."
- Aviation-native alternative: "Engine makers alert on their own engines, and MRO software adds AI inside its own suite. Cleris pulls one engine's sensor, records, spares, slot and manual evidence into one brief, and code checks every number before the engineer signs."

"Is built to" matters: the demo reads team-seeded synthetic sources, not a real shop's AMOS or SAP system. The contrast Cleris can defend has three parts, which no single competitor reviewed publicly combines: data is queried where it lives, not copied into a platform or ontology; the scope is the engine stay-or-remove decision; code, not the model, decides whether the brief passes before a human signs.

**Status: partly built.** The VALUE deck (slide 2 notes and the new value-proposition slide) and the demo narration use the corrected lines since 26 Oct 2026. `CLAUDE.md` still carries the old line.

### 6.4 OEM care contracts limit the airline engineer's decision

**Finding.** Rolls-Royce TotalCare and Pratt & Whitney EngineWise Comprehensive come bundled with engine orders, as with Philippine Airlines' Trent XWB-97s and Wizz Air's GTFs. Under those contracts the OEM carries the engine risk and often controls removal timing. The engineer with the most independent stay-or-remove authority works on engines outside OEM care: mature CFM56 and V2500 fleets, independent MROs and lessors. FTAI Aviation, a CFM56 and V2500 engine MRO and exchange business, put its maintenance footprint on Palantir AIP in Nov 2025, which requires data to be integrated into Foundry. The research report marks the link between contracts and decision authority as an inference.

**Sources.**

- [Aviation Business News, Wizz Air and Pratt & Whitney](https://www.aviationbusinessnews.com/mro/latest-news-mro/paris-air-show-2025-wizz-air-and-pratt-whitney-agree-gtf-engines-deal/) (Jun 2025)
- [Aviation Business News, Philippine Airlines and Rolls-Royce](https://www.aviationbusinessnews.com/farnborough-international-airshow-2026/fia-2026-rolls-royce-lands-pal-a350-1000-trent-xwb-97-engine-order-and-care-deal) (Jul 2026)
- [Business Wire, Palantir and FTAI Aviation](https://businesswire.com/news/home/20251117324507/en/Palantir-and-FTAI-Aviation-Enter-Strategic-Partnership-to-Accelerate-the-Transformation-of-Aircraft-Engine-Maintenance-Repair-and-Exchange) (17 Nov 2025; company claim)
- [Palantir Foundry platform overview](https://www.palantir.com/docs/foundry/platform-overview/overview/index.html) (undated; vendor documentation)

**What it means for Cleris.** Name the target user as the powerplant engineer for engines outside OEM care contracts (airline engine bases, independent MROs, lessors), and compare Cleris with the cost and time of onboarding to a platform, not with copilot features.

**Status: not built.** The README and `AGENTS.md` describe the user as a maintenance engineer, with no statement on OEM care contracts or target engines.

## Limits of this research

- Almost every competitor figure is a vendor or customer claim. None was audited, and no incumbent publishes removal-forecast accuracy or false-alarm rates in a comparable form.
- A capability missing from public material may exist inside a company. Boeing's TechOps AI Agent has been announced but not described and should be rechecked.
- The EASA concept paper is non-binding guidance under consultation; NPA 2025-07 was not read in full; the current consolidated Part-145.A.50 text was not checked; the FAA roadmap PDF could not be opened.
- Every Cleris result comes from a six-day hackathon prototype on NASA C-MAPSS simulated engines and synthetic maintenance data. The 311 of 311 result is a self-test, and the 97% fault-mode figure is agreement with a proxy label.
