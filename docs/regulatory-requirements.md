# Regulatory requirements for Cleris

26 Oct 2026. Cleris is a hackathon prototype on NASA C-MAPSS simulated telemetry and synthetic maintenance, spares and document data. It is not certified or approved by any aviation authority and not for operational use. This page summarises what EASA, FAA, ICAO and industry rules require of an airline, CAMO or MRO that uses a decision-support tool like Cleris, where Cleris stands, and what closes the gaps. It is condensed from a 26-requirement review (2 met, 15 partly met, 9 not met). It is not legal advice; the operator's authority decides acceptance.

## How a tool like Cleris is accepted

No EASA, FAA or ICAO rule certifies or approves a ground-based maintenance decision-support tool. The operator accepts it through its own approved system:

- **Exposition or manual.** EU: MOE (including item 2.21, computer record systems) or CAME. US: repair-station, QC or carrier manual. The tool's functions, users, credential administration, degraded mode and records system are described there.
- **Management of change and safety risk management.** EU: AMC1 145.A.200(a)(3)(e), 145.A.85, CAMO.A.130. US Part 121: 14 CFR 5.51–5.55.
- **Records.** 145.A.55 and CAMO.A.220 with AMC1 145.A.55(c): 24-hour backup, protection against alteration. US: 14 CFR 121.369(c), 121.380 and 145.219.
- **Information security.** Part-IS (Reg (EU) 2023/203) has applied to Part-145 organisations and CAMOs since 22 Feb 2026, and its AMC names AI/ML as an example of a significant change.
- **Occurrence reporting.** Reg (EU) 376/2014, 145.A.60 and CAMO.A.160 (72 h); 14 CFR 121.703 and 145.221 (96 h). Reporting stays with people.

EASA's 2023 electronic-signature guidelines (non-binding) say authorities are not expected to audit the software itself and that the developer of an internal workflow tool is not a subcontractor. That holds while the operator's staff run Cleris. If Cleris were sold as an analysis service, AMC1 CAMO.A.125(d)(3) would make the provider a subcontractor inside the CAMO's management system.

## The boundary Cleris stays inside

- **Advisory decision support.** The engineer decides. This review reads Cleris as EASA Level 1B in the non-binding Concept Paper Proposed Issue 03. LLMs stay off the decision path, which matches EASA's limit of large off-the-shelf models to the lowest assurance level.
- **No maintenance credit.** Cleris output is never the sole source of an airworthiness determination or a programme interval change (FAA AC 43-218 and OpSpec D302; MSG-3 IP 180/197). It never substitutes for a required inspection item. Limits come from the operator's approved data.
- **Never a release to service.** Certifying staff issue the release (Part-145.A.50; 14 CFR 43.7, 43.9, 121.709, 145.213). Cleris never produces a CRS, EASA Form 1 or FAA Form 8130-3. Approval drafts a work order for the organisation's own release process.

## Sign-off and the decision log (26 Oct 2026)

What is built:

- **Signer.** Approve and Reject need a typed "Signed by" name. Role and licence reference are optional (`casefile/decision_log.py` `Signer`; `ui/app.py` `_sign_off`).
- **Binding to the brief.** The entry records UTC time and a `BriefRef` with case ID, run ID and the SHA-256 of the recommendation. The form shows the case ID and fingerprint before signing.
- **Escalations.** Approve needs every escalation ticked as read. Reject needs none, and any ticks are recorded (`tools/writeback.py` `approve_case`; `ui/brief.py` `approval_gate`).
- **Log.** Each case has an append-only JSONL log at `.state/decision_logs/<case_id>.jsonl` with a SHA-256 hash chain. `verify_rows` detects edited, deleted, reordered or cut entries, and a correction is a new entry.
- **Work-order record.** The draft record carries `signed_by`, `signed_at`, `brief`, `decision_log` (path, head hash, entries) and `release_note`: "Draft work order for the organisation's own release process; not a certificate of release to service."
- **Added later on 26 Oct.** The brief as shown is stored at sign-off (`.state/briefs/<case_id>-<sha12>.json`) and its SHA-256 is in the engineer's entry; every stored entry carries a version stamp (code commit, asset pack version, model file hashes, the model or "recorded"/"none"); the head hash is anchored in the case-memory record and the run's trace, and verification checks both; inspection findings entered by an inspector are logged as their own entries; a "potentially reportable" note flags conditions for the operator's reporting scheme without filing anything.
- **Added after an engine engineer's review (26 Oct, late).** Each mandatory document now gets a method-of-compliance line on the plan and the draft work order (removal before the limit, or the task by a date). A rule check sends back a plan with no line or a late one, and two seeded faults test it. Work-package templates mark required inspection items (RII) on synthetic task references. A recommended service bulletin is no longer followed by precedence alone: engineering adopts or declines it once per fleet in a signed, hash-chained engineering order, and until then the plan follows the manual section for the task.

Not yet:

- The name is typed, not authenticated, and not checked against an authorisation list.
- The log, the anchors and the brief snapshots are local files with no backup or retention policy; the chain is tamper-evident, not tamper-proof.
- The live CMMS row keeps its 10 columns, with no signer or case link.

## Gap table

Types: IR = binding rule; AMC/GM = EASA soft law; AC = FAA advisory circular; G = non-binding guidance; Std = industry standard.

| # | Requirement | Reference (type) | Status | Evidence | Next step |
| --- | --- | --- | --- | --- | --- |
| 1 | Release to service stays with certifying staff | 145.A.50 (IR); 14 CFR 43.7, 43.9, 121.709, 145.213 (IR) | Met | `tools/writeback.py` `RELEASE_NOTE`; `casefile/decision_log.py` `SIGNOFF_NOTE` | Define or drop "clearance" in the product line |
| 2 | Approver identified, authenticated and authorised | 14 CFR 121.369(c) (IR); AMC1 145.A.55(d) (AMC); AC 120-78B §2 (AC); EASA e-signature guidelines §4.5 (G) | Partly met | Typed name, optional role and licence; no login or role check | Operator single sign-on; roles from the authorisation list |
| 3 | Approval reaches the operator's work order | CAMO.A.315(b)(6),(d)(2) (IR) | Partly met | JSONL record complete; CMMS row has no signer or case link (`_insert_cmms`) | Approver, case ID and head hash in the CMMS |
| 4 | Advisory only, no maintenance credit | AC 43-218, OpSpec D302 (AC); IP 180/197 (Std); CAMO.A.315(b)(4) (IR) | Partly met | No intended-use statement; "Continue in service" wording; an unadopted SB set an AMM interval by precedence (fixed 26 Oct: engineering order per fleet) | Intended-use statement; reword; SB items for CAMO assessment |
| 5 | LLM off the decision path | EASA Concept Paper Proposed Issue 03 (G) | Met | `agents/critic.py`, `assurance/challenges.py`; LLM-alone critic 3 of 41 | Re-run `eval-llm` when the served model changes |
| 6 | Current data; revision recorded | 145.A.45, CAMO.A.325, 14 CFR 145.109(d) (IR); AMC1 145.A.55(a)(3) (AMC) | Partly met | `tools/knowledge.py` `in_effect`; synthetic documents; fixed reference date | Controlled library; revisions in the sign-off entry |
| 7 | ADs, LLPs and RIIs as hard constraints | 14 CFR Part 39, 121.380(a)(2), 121.369(b)(8) (IR); GM1 145.A.50(a) (GM); AC 43-9D (AC) | Partly met (AD, RII labels) / not met (LLP) | `assurance/rules.py` `mandates_honoured` on one synthetic AD, with a method of compliance; RII labels in synthetic work-package templates; no LLP data | AD applicability and status from records; LLP limits; RII labels |
| 8 | Tamper-evident decision records | 145.A.55, CAMO.A.220, 14 CFR 145.219, 14 CFR 3.403/3.405 (IR) | Partly met | Hash-chained log with anchored head hash and a stored brief snapshot; draft file and CMMS rows rewritable | Operator records store with backup; INSERT-only grant |
| 9 | Backup and retention | AMC1 145.A.55(c), AMC1 CAMO.A.220 (AMC); 145.A.55 (3 and 5 years), 14 CFR 145.219 (2 years) (IR) | Not met | `.state/` git- and docker-ignored; `docker run --rm` | Operator-designated store with 24-hour backup; retention policy |
| 10 | Software and version control | AC 120-78B §3.2 (AC); 14 CFR 5.51(b) (IR) | Partly met | Version 0.1.0; no model or prompt hash in entries | Stamp commit, pack, model and prompt versions on every entry |
| 11 | Managed change and exposition text | AMC1 145.A.200(a)(3)(e), 145.A.85, MOE 2.21 (AMC/IR); 14 CFR 5.51–5.55, 145.207–145.211 (IR) | Not met | Evaluation and stress test exist; no hazard log or template | System description, hazard log, MOE/CAME/manual template |
| 12 | Training and human factors | 145.A.30(e), CAMO.A.315(e), 14 CFR 121.375 (IR); EASA ORG-07 (G) | Partly met | Explanations, glossary, every escalation ticked before Approve | Training note; reason required past urgent escalations; user test |
| 13 | Information security | Part-IS, Reg (EU) 2023/203 (IR); AC 120-78B §3.2 (AC) | Partly met | SELECT-only guard; no auth on UI, API or context service; hosted LLM | Authentication everywhere; non-root image; supplier pack |
| 14 | Occurrence and SDR reporting | Reg (EU) 376/2014, 2015/1018, 145.A.60, CAMO.A.160; 14 CFR 121.703, 145.221 (IR) | Not met | Escalations on screen only | "Potentially reportable" flag handed to the operator |
| 15 | Programme feedback (CASS, reliability, ECM) | 14 CFR 121.373, 121.374 (IR); AC 120-17B, 120-79A (AC) | Not met | No outcome capture | Log recommendation, decision and outcome; pilot outside ETOPS ECM |
| 16 | EU AI Act | Reg (EU) 2024/1689 Art. 4 (IR) | Partly met | Not high-risk on this reading; no AI-literacy material | AI-literacy section in the training note |

## Prioritised actions

Effort: S = about a day; M = 1–2 weeks; L = a month or more, operator-dependent. These are estimates.

### Done for the hackathon

- Release note in record, log and UI; README disclaimer.
- Typed signer, UTC time, recommendation SHA-256.
- Escalation acknowledgement before Approve, and a typed reason to approve past an urgent escalation.
- Hash-chained log per case, attached to the draft.
- LLM off the decision path; AD mandate rule; revision filter; SELECT-only gateway.
- Intended-use statement and a definition of "clearance" (README and help panel); the help text now says what is sent to Nightshift in live mode before approval.

### Before a pilot with an operator

- Single sign-on and roles from the operator's authorisation list (M).
- Durable records: operator store with 24-hour backup, head hash and signer in the CMMS, INSERT-only grant, supersede entries (M). The brief snapshot and version stamping on every entry are done.
- Boundary wording: healthy-engine action, SB precedence (S).
- One fleet's real data: controlled documents, real date, AD status, LLP, RII (L).
- Hand-off of the "potentially reportable" note into the operator's reporting system, and a data-issue export (M). The note itself is built.
- Operator change pack: system description, hazard log, exposition template, Part-IS supplier pack, training and AI-literacy note (M).
- Authentication on the context service and API; non-root container (S–M).
- Pilot design: operator-run software, non-ETOPS scope, outcome logging (M).

### Before operational use

- Signature in the operator's M&E system, or the full AC 120-78B / A025 path (L).
- Operator's management of change and SRM accepted; exposition amended; authority informed; internal audit within three months; ISMS update (L).
- Validation on real fleet data under controlled service introduction (L).
- Retention per CAME/MOE; exports in Spec 2000 Ch 18, Ch 11 and Spec 2500 form (M–L).
- CASS and reliability integration; ECM description and CHDO review if used in ETOPS (L).
- Recurring ADs, AMOC gate, since-new LLP records (L).
- Per-release change log with record-impact statement (M).
- Watch RMT.0742's second NPA, ED-324 and the AI Act 2028 date (S, ongoing).

## Binding rules and guidance

- **Binding:**
  - EU: Reg (EU) 1321/2014 (Part-M, Part-145, Part-66, Part-CAMO), Reg (EU) 2023/203 (Part-IS), Reg (EU) 376/2014 and 2015/1018, Reg (EU) 2024/1689.
  - US: 14 CFR Parts 3, 5, 39, 43, 91, 121 and 145, plus OpSpecs A025/D302 once issued.
  - ICAO Annexes 6, 8 and 19 bind States, not operators.
- **Guidance:** EASA AMC/GM; EASA electronic-signature guidelines (2023); EASA AI Concept Paper Proposed Issue 03; NPA 2025-07 (proposal); FAA ACs 120-78B, 43-218, 43-9D, 120-16G, 120-17B, 120-79A, 120-42B, 39-9, 120-77B, 120-92D; FAA AI Roadmap Version I.
- **Industry:** MSG-3 IP 180/197 (policy only once an authority adopts them); ATA Spec 2000, 2500 and 42; SAE ARP5120, ARP5987B and ARP7122; ED-324/ARP6983 (unpublished, airborne and ATM/ANS only).

## Not verified at source

- Verbatim EU IR text for most Part-145 points; summaries from EASA's compliance guide and AMC were used.
- Part-IS I.OR text.
- Digital Omnibus Reg (EU) 2026/1744 (secondary source).
- EU original of Reg 376/2014 Art. 4 (UK copy used).
- Part-66 amendments of 2026.
- FAA Order 8900.1.
- ICAO Annex wording.
- MSG-3 revision status; ATA and SAE contents (abstracts).
- Any authority position on accepting AI planning tools.

## Consistency with research-findings.md

- **Finding 1.1:** still partly built. The decision log adds signer, time and brief hash; authentication is missing.
- **Finding 1.2:** consistent. Built on synthetic documents; real applicability is not built.
- **Finding 1.3: correction.** The FAA AI Roadmap (Version I, 23 Jul 2024) was read at source. It mentions maintenance descriptively (digital twins, predictive maintenance) and has seven principles.
- **Finding 2.1:** the ORG-06 row is now partly addressed. The sign-off stores a signer and a recommendation hash, but no copy of the evidence.
- **Finding 6.1:** consistent. The README disclaimer is in; "the MRO clearance assistant" is unchanged.

## Sources

EU and EASA:
- [EASA guide to Part-145 as amended by Reg (EU) 2021/1963](https://www.easa.europa.eu/en/downloads/136744/en)
- [AMC/GM to Part-145, Issue 2 Amdt 5](https://www.easa.europa.eu/sites/default/files/dfu/amc_gm_to_part-145_-_issue_2_amendment_5.pdf) and [Amdt 7](https://www.easa.europa.eu/sites/default/files/dfu/amc_gm_to_part-145_issue_2_amendment_7.pdf)
- [Part-CAMO IR](https://www.easa.europa.eu/de/downloads/20843/en)
- [AMC/GM to Part-CAMO](https://www.easa.europa.eu/en/downloads/111323/en)
- [EASA guidelines on electronic documents, records and signatures (2023)](https://www.easa.europa.eu/en/downloads/137906/en)
- [AMC/GM to Part-IS](https://www.easa.europa.eu/ro/downloads/138216/en)
- [ILT Part-IS dates](https://english.ilent.nl/topics/aviation/part-is)
- [Reg (EU) 376/2014 Art. 4 (UK copy)](https://www.legislation.gov.uk/eur/2014/376/article/4)
- [Reg (EU) 2015/1018](https://www.legislation.gov.uk/eur/2015/1018/data.html)
- [EASA AI Concept Paper Proposed Issue 03](https://www.easa.europa.eu/en/downloads/143701/en)
- [NPA 2025-07](https://www.easa.europa.eu/en/newsroom-and-events/news/easas-first-regulatory-proposal-artificial-intelligence-aviation-now-open)
- [AI Act Annex I](https://ai-act-service-desk.ec.europa.eu/en/ai-act/annex-1) and [Article 108](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-108)
- [Digital Omnibus summary](https://artificialintelligenceact.eu/ai-act-explorer/digital-omnibus/)

US and FAA:
- [14 CFR 43.9](https://www.ecfr.gov/current/title-14/section-43.9)
- [Part 3 Subpart D](https://www.ecfr.gov/current/title-14/part-3/subpart-D)
- [121.369](https://www.ecfr.gov/current/title-14/section-121.369)
- [121.380](https://www.ecfr.gov/current/title-14/section-121.380)
- [121.703](https://www.ecfr.gov/current/title-14/section-121.703)
- [121.709](https://www.ecfr.gov/current/title-14/section-121.709)
- [145.109](https://www.ecfr.gov/current/title-14/section-145.109)
- [145.213](https://www.ecfr.gov/current/title-14/section-145.213)
- [145.219](https://www.ecfr.gov/current/title-14/section-145.219)
- [145.221](https://www.ecfr.gov/current/title-14/section-145.221)
- [Part 39 (39.7)](https://www.ecfr.gov/current/title-14/section-39.7)
- [Part 5 (5.51)](https://www.ecfr.gov/current/title-14/section-5.51)
- [AC 120-78B](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_120-78B_FAA_Web.pdf)
- [AC 43-218](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_43-218_Ed_Upd_%2812-19-25%29.pdf)
- [AC 43-9D](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_43-9D_Ed_Upd.pdf)
- [AC 120-17B](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_120-17B_Ed_Upd_%287-9-26%29.pdf)
- [AC 120-92D](https://www.faa.gov/documentLibrary/media/Advisory_Circular/AC_120-92D_FAA_Web.pdf)
- [FAA AI Roadmap](https://www.faa.gov/aircraft/air_cert/step/roadmap_for_AI_safety_assurance)

ICAO and industry:
- [ICAO EAMR](https://icao.int/safety/OPS/OPS-Section/Pages/EAMR.aspx)
- [IP 180](https://www.easa.europa.eu/download/imrbpb/IP%20180%20-%20AHM%20integration%20in%20MSG-3.pdf)
- [IP 197](https://www.easa.europa.eu/sites/default/files/dfu/ip_197_-_amendment_to_ip180.pdf)
- [IATA AHM white paper](https://www.iata.org/contentassets/fafa409c883d41198aeb87628c848851/ahm-wp-1sted-2022.pdf)
- [ATA Spec 2000 Ch 18](https://publications.airlines.org/products/spec-2000-electronic-work-package-ch-18-revision-2023-1)
- [ATA Spec 42](https://publications.airlines.org/products/spec-42-aviation-industry-standards-for-digital-information-security-revision-2026-1)
- [EASA AI Days 2026 (ED-324 status)](https://www.easa.europa.eu/en/downloads/144162/en)
