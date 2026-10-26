# Converting One Removal a Year Repays Cleris

> Research report, 26 Oct 2026, compiled for the hackathon from public sources (links inline) and this repository's evaluation. Cleris figures are measured on NASA C-MAPSS simulated engines with synthetic maintenance data; vendor capabilities are as published, not independently tested.

Modelled on published industry costs, Cleris's value for a reference airline with 100 narrowbodies is real but thin. In the base case it creates about **$188,000 a year of gross value**, roughly **$1,900 per aircraft**. After the cost of its own false alarms and of running it, the base case nets about **−$40,000 a year**: effectively break-even. The scenario range runs from **−$0.72M a year (conservative) to +$2.79M a year (upside)**. Almost all of the value comes from one mechanism: turning an unscheduled engine removal into a planned one. In the base case that is worth about **$133,000 per event** ($536,000 upside), once cancellations, aircraft-on-ground (AOG) time, short-notice lease premiums, emergency logistics and the chance of secondary damage are weighed against the green time lost by pulling the engine early. Airlines already run engine health monitoring, so only removals that this monitoring plus engineer judgement would have missed count. Cleris pays for its running cost and its own false alarms at about **1.1 such extra conversions a year**, which is 6% of the roughly 18.5 unscheduled removals this fleet sees. Counting integration cost, it needs 1.6 a year; the base-case estimate is 0.83. Engineer time and audit effort, the levers a pitch reaches for first, are worth under $11,000 a year combined. The results turn mainly on running cost, the share of catches credited to Cleris, removal timing against parts and hangar slots, and secondary damage. Cleris's caution matters too. Its measured 1-in-102 early-pull rate, applied to a real fleet, produces about as many early-pull calls as extra catches. Every dollar in this report is modelled. Cleris has measured its safety record (45 of 45 near-failure simulated engines sent for removal, swap or repair) and the price of that caution (41 of 102 healthy engines sent for inspection, one pulled early). It has measured no time or cost saving, and its tests ran only on NASA's simulated engines with synthetic maintenance data. The pitch should therefore sell what one converted removal is worth and the break-even threshold, not an annual saving.

## Engine timing became a six-figure decision in 2026

The engine market of 2025–26 has turned the timing of a removal into money. Shop turnaround has stretched from the historical 30–60 days to about 75 days for a full overhaul, and upward of 300 days in some cases. 80% of MRO executives blame piece-part availability. Excess engine leasing cost airlines about **$2.6bn in 2025**, and each extra 10 days of shop time adds roughly $0.8–0.9bn industry-wide ([IATA/Oliver Wyman, Oct 2025](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/reviving-the-commercial-aircraft-supply-chain.pdf)). Short-term LEAP and PW1100G leases have passed **$6,500 a day**, against about $5,000 in 2022–23 ([IBA via Reuters/AJOT, Aug 2026](https://www.ajot.com/news/airlines-get-grounded-jets-flying-again-but-engine-bills-linger)). At the March 2025 peak, 648 GTF-powered aircraft, 28% of that fleet, were parked waiting for engines ([IATA/Emerton, Jun 2026](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/single-aisle-aircraft-engines-mro.pdf)). In this market, a removal planned against a known slot, known parts and a known spare costs far less than one forced by an in-service finding.

No published source measures the all-in cost gap between an unscheduled and a planned removal of the same engine. The one headline figure in circulation, "30–50% more", comes from a vendor blog with no method, so this model excludes it ([DatanetIoT](https://www.datanetiot.com/cost-of-aircraft-maintenance-delays/)). The value of a conversion therefore has to be built from parts. In each part a sourced unit cost is multiplied by an assumed frequency, and the frequencies are where the uncertainty lives.

The disruption part uses EUROCONTROL's day-of-operation cancellation cost: **€25,720 for a 180-seat narrowbody and €16,640 for a 120-seat one** (2022 prices, including passenger care and EU261 compensation) ([EUROCONTROL Standard Inputs](https://ansperformance.eu/economics/cba/standard-inputs/chapters/cancellation_cost.html)). These are converted at an assumed 1.05 USD/EUR to $27,006 and $17,472. AOG days are priced at the aircraft ownership floor: about **$400,000 a month** to lease a new A320neo or 737 MAX, or $13,151 a day ([Cirium](https://cirium.com/thoughtcloud/ascend-consultancy-weekly-team-perspective-impact-of-rising-interest-rates-lease-fees)). IBA put these leases at $316–324k a month in 2022 ([AirlineGeeks](https://airlinegeeks.com/?p=60701)). A narrowbody flying the six daily sectors typical of European low-cost operations ([EUROCONTROL Data Snapshot #30](https://www.eurocontrol.int/publication/eurocontrol-data-snapshot-30-daily-utilisation-aircraft-type)) would lose up to about $162,000 a day if every sector were cancelled. The model assumes far fewer cancellations than that.

The lease part prices the gap between short-notice and planned spare-engine cover. Spot rates run above $6,500 a day, while IBA's term rate for a LEAP-1A is **$145–155k a month**, about $4,800–5,100 a day ([IBA, Sep 2024](https://www.iba.aero/resources/articles/engine-values-lease-rates-september-2024/)). A same-date appraisal of the LEAP-1B shows $6,000–7,000 a day short-term against $130–150k a month long-term ([ISTAT/IBA, 2025](https://www.istat.org/ISTAT-Online/ISTAT-Online/Aircraft/ArtMID/1205/ArticleID/1639/Appraisal-CFM-LEAP-1B)). The model takes a premium of $1,500 a day in the base case and $1,900 in the upside. Emergency air freight of an engine costs $10–50k, a vendor figure without method ([Air Cargo Week, Mar 2026](https://aircargoweek.com/the-true-cost-of-engine-maintenance/)).

Secondary damage, the tail risk, has three anchors. Koops's peer-reviewed engine business case uses **$500,000 of contingency damage** after an in-flight failure ([Koops, PHME 2018](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf)). The FAA costs an on-condition LEAP-1A HPT stage-1 blade replacement at **$1,000,950 per engine** ([FAA AD 2022-17-12](https://www.govinfo.gov/content/pkg/FR-2022-09-01/pdf/2022-18923.pdf)). And replacing life-limited parts in quantity can "double or more" the direct cost of a shop visit ([IATA/Oliver Wyman](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/reviving-the-commercial-aircraft-supply-chain.pdf)).

Against these benefits stands the green time lost by pulling an engine before it fails. Aircraft Commerce puts equivalent maintenance reserves at **$290–500 per engine flight cycle (EFC)** for a CFM56-3 ([Aircraft Commerce, 2017](https://www.aircraft-commerce.com/sample_article_folder/111_TRD.pdf)).

| Per converted removal (USD) | Formula | Conservative | Base | Upside |
|---|---|---|---|---|
| Disruption and AOG | cancellations × cost + AOG days × $13,151 | 0.5 × 17,472 + 0.5 × 13,151 = **15,311** | 1 × 27,006 + 1 × 13,151 = **40,157** | 4 × 27,006 + 3 × 13,151 = **147,476** |
| Short-notice lease premium | days × premium | 0 × 1,500 = **0** | 30 × 1,500 = **45,000** | 75 × 1,900 = **142,500** |
| Emergency logistics | flat | **0** | **10,000** | **50,000** |
| Secondary damage avoided | probability × cost | 2% × 500,000 = **10,000** | 5% × 1,000,000 = **50,000** | 10% × 2,000,000 = **200,000** |
| Green time given up | − EFC × $/EFC | − 60 × 500 = **−30,000** | − 30 × 400 = **−12,000** | − 15 × 300 = **−4,500** |
| **Value per conversion, ΔC** | sum | **−4,689** | **133,157** | **535,476** |

The conservative column shows what the base and upside hide. If the operator has a spare engine on hand, removes the engine at its main base, cancels at most one flight, and gives up two weeks of green time, converting the removal is worth **nothing**. The value of planning sits in the tails: missing spares, multi-day AOG and secondary damage. In the base case, secondary damage and the short-notice lease each supply about a third of ΔC, and disruption another 30%.

## Time savings are small; caution and running cost are not

Three other levers add to the conversion value, and three costs come out of it.

**Lever B, spare-engine cover timing**, applies the planner's discipline to every removal, not only the converted ones. Cleris's planner checks stock, lead time and the first free hangar slot. It switches to a lease-engine swap when the remaining-life bound is shorter than lead time plus slot wait ([maint_planner.py](../agents/maint_planner.py)). Lining every removal up with parts and slots could trim days of spare cover. The model values this at α × removals × days saved × daily cover cost. The base case assumes 2 days saved at $5,000 a day, the IBA LEAP-1A term rate rounded. The number of days is an assumption, and this lever turns out to be the third-largest driver of the result.

**Lever C, engineer time**, has the best evidence and the least money. Technicians spend up to 30% of their time searching manuals, and in a controlled study with 10 licensed technicians, retrieval cut task lookup from **6–15 minutes to 18 seconds** ([Jo, EACL 2026](https://aclanthology.org/people/byungho-jo/)). McKinsey puts research and report preparation at about 60% of a technician's day ([McKinsey, Apr 2024](https://www.mckinsey.com/industries/aerospace-and-defense/our-insights/the-generative-ai-opportunity-in-airline-maintenance)). In a randomised trial, consultants using GPT-4 finished in-scope tasks **25.1% faster**, but were 19 percentage points less likely to be correct on tasks outside the model's competence ([Dell'Acqua et al.](https://mitsloan.mit.edu/sites/default/files/2023-10/SSRN-id4573321.pdf)). GE reports, without independent verification, gathering CFM56 shop-visit records about 25% faster with an AI tool ([Benzinga](https://www.benzinga.com/node/62480151)).

The model ties the saving to Cleris's actual job, the engine disposition decision. GE issues more than 18,000 customer notification reports a year across 30,000–44,000 monitored engines, about **0.4–0.6 per engine** ([GE Aerospace](https://www.geaerospace.com/collaborative-insight)). The model therefore assumes 0.6 engine decisions per engine-year, or 120 for 200 engines. Each decision takes an assumed 2 engineer-hours, and Cleris saves 40% of that. Labour is priced at $68.58 an hour: a $100,000 base salary, the midpoint of an airline powerplant-engineer posting at $83,500–113,500 ([Atlas Air](https://careers-atlasair.icims.com/jobs/6886/powerplant-engineer/job)), loaded at BLS's 29.9% benefits share ([BLS ECEC](https://www.bls.gov/news.release/archives/ecec_03202026.htm)) over 2,080 hours. The result is **$6,584 a year**.

A generous whole-job view gives a similar order of magnitude. It takes the BLS median aerospace engineer at $134,960 ([BLS](https://www.bls.gov/ooh/architecture-and-engineering/aerospace-engineers.htm)), loaded to $192,525, and assumes 20–35% of time spent gathering information with 25–50% of that recovered. That still yields only $9,600–33,700 per engineer-year. The saving is also capacity, not cash, unless headcount actually falls.

**Lever D, compliance and audit**, behaves like insurance. The FAA can fine an operator **$75,000 per violation** and often counts each non-compliant flight separately ([Federal Register, Dec 2024](https://www.govinfo.gov/content/pkg/FR-2024-12-30/html/2024-30608.htm)). Proposed penalties for airworthiness directive (AD) lapses range widely:

| Operator | Proposed penalty | Context |
|---|---|---|
| Brazos Valley Air Charter | $202,450 | ([FAA](https://www.faa.gov/newsroom/faa-proposes-202450-civil-penalty-against-brazos-valley-air-charter-tulsa-oklahoma)) |
| Kalitta Air | $400,000 | 44 flights ([FAA](https://www.faa.gov/newsroom/faa-proposes-400000-civil-penalty-against-kalitta-air-llc)) |
| Southwest | $10.2M, settled at $7.5M | ([AIN](https://ainonline.com/aviation-news/business-aviation/2008-03-11/faa-willing-accept-102-million-southwest)) |
| American | $24.2M | Alongside more than 3,000 cancelled flights ([NPR](https://www.npr.org/sections/thetwo-way/2010/08/26/129451725/american-airlines-faa-24-2-mln-fine-for-md-80-inspection-failures)) |

Cleris keeps AD-mandated tasks locked in the plan, and it caught 8 of 8 seeded "dropped mandate" faults ([Cleris evaluation report](../evaluation/report.md)). Even so, the expected value is small. An assumed 1% annual chance of a $400,000 engine-AD lapse, with 25% of that risk removed, is worth $1,000 a year. Records-pack time on 23 removals a year adds another $3,169 (8 hours each, 25% saved, using GE's figure). The real value of this lever is protection against a rare catastrophe, not annual cash.

**Cleris's caution is the first cost.** On 200 simulated engines, the decisions came out as follows ([Cleris evaluation report](../evaluation/report.md)). Cleris converts cycles to days at an assumed 2 cycles per day ([AGENTS.md](../AGENTS.md)).

| True remaining life | Engines | Removal, swap or repair | Inspection | Kept flying |
|---|---|---|---|---|
| Fails within 15 days | 45 | 45 | 0 | 0 |
| 15–40 days | 53 | 14 | 38 | 1 |
| More than 40 days | 102 | **1** (pulled early) | **41** | 60 |

Two figures in the table are the price of caution: **41 inspections and 1 early pull among the 102 healthy engines**. One engine in the 15–40 day band was told to keep flying, which is the kind of miss the model's imperfect detection rate stands for.

These are snapshot rates on a simulated fleet, in which every engine is running toward failure. They are not annual rates. The model translates them by assuming Cleris is run once per engine alert: 120 decisions a year, of which an assumed 15% concern engines genuinely near removal. That leaves **102 decisions a year on non-failing engines**, the same count as the simulated healthy group, so the measured 41 and 1 carry over one-for-one as **41 false inspection calls and 1 early-pull call a year**.

For inspections, the FAA costs a targeted borescope at **4 work-hours × $85 = $340** ([FAA AD 2022-17-12](https://www.govinfo.gov/content/pkg/FR-2022-09-01/pdf/2022-18923.pdf)), and a routine borescope can take an aircraft out of service for 12 hours ([Rolls-Royce, 2021](https://www.rolls-royce.com/media/our-stories/discover/2021/intelligentengine-harnessing-the-power-of-ai-to-deliver-more-intelligent-engine-inspections.aspx)). The base case therefore assumes $1,000 of labour plus a 3% chance of cancelling a sector, or $1,810 per call, with 75% of calls acted on. For early pulls, the base case assumes $29,000 of removal, installation and freight (the midpoint of a vendor's $8–30k plus $10k) and 160 cycles of green time at $400. That is $93,000 per call, with half of the calls acted on. Together these give **$55,663 for inspections and $46,500 for early pulls** a year in the base case.

The design is the right shape. Koops shows that when a borescope can resolve a false alarm, the cost-optimal false-alarm rate is as high as **0.73**. When false alarms force removals, the optimum is **0.15–0.21**, and rates above about 0.6 destroy the business case in most scenarios ([Koops, PHME 2018](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf)). Cleris's 40% inspection rate and 1% early-pull rate sit inside both bounds.

Prevalence is the problem. In a real fleet, few engines are near failure at any moment, and operators judge alerts by precision: Safran's design target is a false-alert probability below 5% ([Massé et al., PHME 2014](https://papers.phmsociety.org/index.php/phme/article/download/1542/510)). At realistic prevalence, Cleris's 1-in-102 early-pull rate produces **1.0 early-pull call a year against 0.83 extra conversions**. A monthly sweep of all 200 engines at the simulated rates would generate about **80 inspection calls per sweep, 965 a year**, which no operator would accept. Cleris must either run on alerts or be recalibrated on operator data before it runs fleet-wide.

**Running cost is the second cost, and the least evidenced.** No aviation predictive-maintenance vendor publishes per-engine pricing. Pratt & Whitney Canada's $7,500 per engine per year bundles maintenance with health insights, so it is a ceiling, not a benchmark ([AFRAA/P&WC](https://www.afraa.org/pratt-whitney-canada-introduces-pwcsmart-subscription-service-providing-pt6a-engine-maintenance-and-services-with-simple-annual-fee/)). The model uses Feldman's academic infrastructure baseline of **$450 per monitored unit per year**, tested between $100 and $1,000 ([Feldman thesis, 2008](https://drum.lib.umd.edu/items/752d334b-8a75-49d9-b266-a8ad866a4667)), plus an assumed 0.25 internal engineer to maintain the asset pack and data feeds. That comes to **$125,663 a year** in the base case. One-off integration is assumed at $300,000, since no public benchmark exists. Licence fees are left out on purpose: the model measures the value pool, and the headroom left for a price is a result, not an input.

## Today's engine monitoring, not fix-on-failure, sets the baseline

The counterfactual is the most consequential modelling choice, and this model fixes it as **existing engine health monitoring plus engineer judgement**. That baseline is not hypothetical. GE monitors more than 44,000 engines and issues more than 18,000 notification reports a year ([GE Aerospace](https://www.geaerospace.com/collaborative-insight)). OEMs also report large gains from their own analytics: GE says one carrier cut unscheduled removals by 56% ([GE keynote, PHM 2019](https://phmsociety.org/wp-content/uploads/2019/05/2019-Keynote-Jon-Dunsdon.pdf)), and Emirates cut unscheduled maintenance by 50% ([GE Reports, 2019](https://www.ge.com/news/reports/getting-air-time-software-helps-emirates-keep-planes-running/)). Both are self-reported.

Cleris's two fault modes, gradual HPC and fan degradation, are exactly the trend signatures that monitoring already watches. The classic PHM return-on-investment method compares against fix-on-failure ([Feldman, 2008](https://drum.lib.umd.edu/items/752d334b-8a75-49d9-b266-a8ad866a4667)). Used here, it would credit Cleris with every catch. In the base case that alone turns **−$40,000 into +$355,000 a year**, an overstatement of nearly $0.4M.

Three multipliers separate the catches Cleris could claim from the removals that happen anyway. **Coverage (s)** is the share of unscheduled removals that fall in failure modes Cleris models: 10%, 25% and 40% across the conservative, base and upside cases. The prototype models two modes; for scale, HPT blade failures alone account for 6–18% of unscheduled removals in Koops's data. **Detection (TPR)** is the share of those removals caught early enough to plan: 0.4, 0.6 and 0.8, deliberately well below the 45 of 45 Cleris achieved on simulated engines. **Attribution (α)** is the share of correct catches that the existing process would have missed: 0.10, 0.30 and 0.50. All three take the lower end of the ranges in published PHM scenario practice ([Koops, 2018](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf); [FTI, 2013](https://phmsociety.org/wp-content/uploads/2013/06/Making-the-Business-Case-for-PHM-2013_0.pdf)). Together they convert **0.4%, 4.5% and 16%** of unscheduled removals, well below the 50–56% OEM programmes claim against their own pre-analytics baselines.

Attribution also applies to lever B, because planners already align removals with slots. It does not apply to engineer time, which is measured against the current process. False calls get no such discount. The model charges Cleris for every false call an engineer acts on, even one the existing process might also have raised. The "acted-on" shares above reflect only engineers overriding weak calls at sign-off.

Adoption takes time. Honeywell's engine-monitoring cost-benefit study assumes cumulative acceptance of **2.5% in year 1, 16% in year 3, 50% in year 4 and 84% in year 5** ([Gordon et al., PHM Society](https://papers.phmsociety.org/index.php/phmconf/article/download/2170/1168)). Proofs of concept need about two years of historical sensor data for acceptable accuracy ([Aviation Week, 2022](https://aviationweek.com/mro/emerging-technologies/airlines-using-predictive-maintenance-reap-big-benefits)). Data access is the top barrier for 84% of MRO respondents ([McKinsey](https://www.mckinsey.com/featured-insights/charts/ai-wings-for-fleet-fixes)). Because Cleris is software-only, the model compresses that curve to years 1–5 adoption of 10/30/60/80/80% (conservative), 25/60/85/100/100% (base) and 50/90/100/100/100% (upside). Adoption scales benefits and false alarms alike. Running cost is paid in full from year 1, and integration falls in year 1.

## The model nets −$0.72M to +$2.79M a year

The reference operator flies 100 narrowbodies with two engines each at 3,300 flight hours per aircraft per year. That figure sits inside the 2,800–3,900 hour range for A320neo operators ([Aircraft Commerce, 2019](https://www.aircraft-commerce.com/wp-content/uploads/aircraft-commerce-docs/General%20Articles/2019/123_MTCE_B.pdf)), giving **660,000 engine flight hours (EFH) a year**. The unscheduled removal rate (UER) comes from two sources: 0.026 per 1,000 EFH for a mature engine ([Koops, 2018](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf)) and 0.028 for the CFM56-3 ([Aircraft Commerce, 2006](https://www.aircraft-commerce.com/sample_article_folder/maintenance_engineering_1_sample.pdf)). The upside raises the rate by half for a LEAP/GTF-heavy fleet, whose current time on wing is 5,000–6,000 cycles against 15,000–20,000 for mature engines ([IATA/Emerton, 2026](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/single-aisle-aircraft-engines-mro.pdf)).

```
EFH       = aircraft × 2 × FH                         (660,000)
N_UER     = EFH/1000 × UER          N_rem = EFH/1000 × SVR
N_conv    = N_UER × s × TPR × α
ΔC        = n_canx×C_canx + d_AOG×C_own + d_lease×P_lease + C_log + P_sd×C_sd − g×C_EFC
A (conversion)      = N_conv × ΔC
B (cover timing)    = α × (N_rem − N_conv) × d_cover × C_cover
C (engineer time)   = 0.6 × engines × h × σ × w
D (compliance)      = P_AD × Sev × r + N_rem × h_audit × σ_audit × w
E (false inspect)   = decisions × (1 − p) × r_insp × a_insp × (L_insp + P_disr × C_canx)
F (early pulls)     = decisions × (1 − p) × r_rem × a_rem × (H_rem + g_rem × C_EFC)
Run                 = engines × c_infra + FTE × $142,653
Net (steady state)  = A + B + C + D − E − F − Run
Net (year t)        = adoption_t × (A + B + C + D − E − F) − Run;  5-year = Σ Net_t − Integration
```

Tags in the table below: **M** = measured in Cleris on simulated data; **S** = sourced from industry (V marks a vendor source without method); **A** = assumption made for this model.

| Parameter | Conservative | Base | Upside | Basis |
|---|---|---|---|---|
| Aircraft / engines / FH per aircraft | 100 / 200 / 3,300 | same | same | Definition; FH S ([Aircraft Commerce 2019](https://www.aircraft-commerce.com/wp-content/uploads/aircraft-commerce-docs/General%20Articles/2019/123_MTCE_B.pdf)) |
| UER per 1,000 EFH | 0.026 | 0.028 | 0.042 | S ([Koops](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf); [Aircraft Commerce 2006](https://www.aircraft-commerce.com/sample_article_folder/maintenance_engineering_1_sample.pdf)); upside A (1.5×) |
| All removals (SVR) per 1,000 EFH | 0.032 | 0.035 | 0.0525 | S 0.032 (Koops); base A = UER ÷ 0.8 (Koops ratio); upside A (1.5×) |
| Coverage s | 0.10 | 0.25 | 0.40 | A |
| Detection TPR | 0.4 | 0.6 | 0.8 | A; M = 1.00 on simulated engines |
| Attribution α | 0.10 | 0.30 | 0.50 | A |
| Cancellations per unscheduled removal | 0.5 | 1 | 4 | A |
| Cost per cancellation | $17,472 | $27,006 | $27,006 | S ([EUROCONTROL](https://ansperformance.eu/economics/cba/standard-inputs/chapters/cancellation_cost.html)) × A 1.05 USD/EUR |
| AOG days per event / cost per day | 0.5 / $13,151 | 1 / $13,151 | 3 / $13,151 | Days A (Koops uses 5 for a widebody); cost S ([Cirium](https://cirium.com/thoughtcloud/ascend-consultancy-weekly-team-perspective-impact-of-rising-interest-rates-lease-fees)) |
| Short-notice lease days / premium per day | 0 / $1,500 | 30 / $1,500 | 75 / $1,900 | Days A (75 = full-overhaul TAT, S [IATA/OW](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/reviving-the-commercial-aircraft-supply-chain.pdf)); premium S ([IBA](https://www.iba.aero/resources/articles/engine-values-lease-rates-september-2024/); [ISTAT/IBA](https://www.istat.org/ISTAT-Online/ISTAT-Online/Aircraft/ArtMID/1205/ArticleID/1639/Appraisal-CFM-LEAP-1B)) |
| Emergency logistics | $0 | $10,000 | $50,000 | S-V ([Air Cargo Week](https://aircargoweek.com/the-true-cost-of-engine-maintenance/)) |
| Secondary damage probability / cost | 2% / $0.5M | 5% / $1.0M | 10% / $2.0M | Probability A; cost S ([Koops](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf); [FAA](https://www.govinfo.gov/content/pkg/FR-2022-09-01/pdf/2022-18923.pdf)); $2.0M A from "double or more" ([IATA/OW](https://www.iata.org/contentassets/85b59d951fc04c1c83fa2aab47824300/reviving-the-commercial-aircraft-supply-chain.pdf)) on a $2.0–4.5M LEAP visit (S-V [Safe Fly](https://safefly.aero/engine-shop-visit-costs-2026/)) |
| Green time per conversion (EFC) / value per EFC | 60 / $500 | 30 / $400 | 15 / $300 | Cycles A; value S ([Aircraft Commerce 2017](https://www.aircraft-commerce.com/sample_article_folder/111_TRD.pdf)) |
| Cover days saved per removal / cost per day | 0 / $5,000 | 2 / $5,000 | 4 / $6,500 | Days A; rate S ([IBA](https://www.iba.aero/resources/articles/engine-values-lease-rates-september-2024/); [Reuters/AJOT](https://www.ajot.com/news/airlines-get-grounded-jets-flying-again-but-engine-bills-linger)) |
| Decisions per engine-year | 0.6 | 0.6 | 0.6 | S/A ([GE](https://www.geaerospace.com/collaborative-insight): 0.41–0.60) |
| Hours per decision / share saved | 1 / 25% | 2 / 40% | 4 / 60% | Hours A; share S/A ([GE](https://www.benzinga.com/node/62480151); [BCG RCT](https://mitsloan.mit.edu/sites/default/files/2023-10/SSRN-id4573321.pdf); [Jo](https://aclanthology.org/people/byungho-jo/)) |
| Loaded engineer cost per hour | $58.30 | $68.58 | $92.56 | S: $85k / $100k ([Atlas Air](https://careers-atlasair.icims.com/jobs/6886/powerplant-engineer/job)), $134,960 ([BLS](https://www.bls.gov/ooh/architecture-and-engineering/aerospace-engineers.htm)) ÷ 0.701 ([BLS ECEC](https://www.bls.gov/news.release/archives/ecec_03202026.htm)) ÷ 2,080 |
| AD-lapse chance / severity / share removed | 0.5% / $0.2M / 10% | 1% / $0.4M / 25% | 2% / $2.0M / 50% | Chance and share A; severity S ([FAA Brazos](https://www.faa.gov/newsroom/faa-proposes-202450-civil-penalty-against-brazos-valley-air-charter-tulsa-oklahoma); [FAA Kalitta](https://www.faa.gov/newsroom/faa-proposes-400000-civil-penalty-against-kalitta-air-llc)), $2.0M A |
| Records hours per removal / share saved | 4 / 25% | 8 / 25% | 16 / 40% | Hours A; 25% S ([GE](https://www.benzinga.com/node/62480151)) |
| Decisions on engines near removal, p | 15% | 15% | 15% | A |
| False inspection rate / early-pull rate | 40.2% / 0.98% | 40.2% / 0.98% | 20.1% / 0.49% | M (41/102, 1/102); upside A (halved by recalibration) |
| Share of false calls acted on (inspect / pull) | 100% / 100% | 75% / 50% | 50% / 25% | A |
| False inspection: labour / chance of a cancellation | $2,000 / 10% | $1,000 / 3% | $340 / 0% | $340 S ([FAA](https://www.govinfo.gov/content/pkg/FR-2022-09-01/pdf/2022-18923.pdf)); rest A |
| Early pull: handling / green time (EFC) | $60,000 / 400 | $29,000 / 160 | $8,000 / 80 | Handling S-V ([Air Cargo Week](https://aircargoweek.com/the-true-cost-of-engine-maintenance/)); cycles A; $/EFC as above |
| Infrastructure per engine-year / internal FTE | $1,000 / 0.5 | $450 / 0.25 | $100 / 0.1 | Infrastructure S ([Feldman](https://drum.lib.umd.edu/items/752d334b-8a75-49d9-b266-a8ad866a4667)); FTE A |
| One-off integration | $600,000 | $300,000 | $150,000 | A |
| Adoption, years 1–5 | 10/30/60/80/80% | 25/60/85/100/100% | 50/90/100/100/100% | A, compressed from Honeywell's S-curve (S [Gordon et al.](https://papers.phmsociety.org/index.php/phmconf/article/download/2170/1168)) |

| Line (USD per year at full adoption) | Conservative | Base | Upside |
|---|---|---|---|
| Unscheduled removals N_UER / all removals N_rem | 660 × 0.026 = 17.16 / 21.12 | 660 × 0.028 = 18.48 / 23.10 | 660 × 0.042 = 27.72 / 34.65 |
| Converted, N_conv | 17.16 × 0.10 × 0.4 × 0.10 = 0.069 | 18.48 × 0.25 × 0.6 × 0.30 = 0.832 | 27.72 × 0.40 × 0.8 × 0.50 = 4.435 |
| A, conversion (N_conv × ΔC) | 0.069 × −4,689 = **−322** | 0.832 × 133,157 = **110,733** | 4.435 × 535,476 = **2,374,943** |
| B, cover timing | 0 days = **0** | 0.30 × 22.27 × 2 × 5,000 = **66,805** | 0.50 × 30.21 × 4 × 6,500 = **392,792** |
| C, engineer time | 120 × 1 × 0.25 × 58.30 = **1,749** | 120 × 2 × 0.40 × 68.58 = **6,584** | 120 × 4 × 0.60 × 92.56 = **26,657** |
| D, compliance and audit | 100 + 1,231 = **1,331** | 1,000 + 3,169 = **4,169** | 20,000 + 20,526 = **40,526** |
| Gross benefit | **2,758** | **188,291** | **2,834,919** |
| E, false inspections | 41.0 × 1.00 × 4,701 = **192,725** | 41.0 × 0.75 × 1,810 = **55,663** | 20.5 × 0.50 × 340 = **3,485** |
| F, early pulls | 1.0 × 1.00 × 260,000 = **260,000** | 1.0 × 0.50 × 93,000 = **46,500** | 0.5 × 0.25 × 32,000 = **4,000** |
| Run cost | 200,000 + 71,327 = **271,327** | 90,000 + 35,663 = **125,663** | 20,000 + 14,265 = **34,265** |
| **Net, steady state** | **−721,293** | **−39,536** | **+2,793,169** |
| Net per aircraft | −7,213 | −395 | +27,932 |
| Years 1–5 net with adoption ramp | −316k, −406k, −541k, −631k, −631k | −104k, −74k, −52k, −40k, −40k | +1,379k, +2,510k, +2,793k, +2,793k, +2,793k |
| **5-year cumulative, after integration (undiscounted)** | **−$3.13M** | **−$0.61M** | **+$12.12M** |

Every line was computed twice: by hand in the cells above and by an independent script. The two agree to the dollar, with rounding only in the displayed intermediates. In the base case, conversion supplies 59% of gross value and cover timing 35%. Engineer time supplies 3.5% and compliance 2.2%. Discounting would deepen the conservative and base losses slightly and trim the upside. Price years are mixed (2011–2026) and not inflated, which understates the older cost anchors.

**How it scales.** Removal events, false calls and decisions all grow with engine count, so the variable result per aircraft per year is fixed: **−$4,500 (conservative), +$861 (base), +$28,274 (upside)**. Running cost decides whether size helps. If running cost is mostly fixed, as for a software layer on existing systems, the base case breaks even at about **146 aircraft**, or about **216** once integration is spread over five years, and reaches +$47,000 a year at 200 aircraft and +$219,000 at 400. If infrastructure instead scales at Feldman's $450 per engine, the base case never breaks even, because its $861 per aircraft is less than the $900 per aircraft for two engines' infrastructure. The conservative case worsens with size, since its false alarms scale and its benefits barely exist, while the upside scales almost linearly, to about $5.6M at 200 aircraft.

For widebodies, every per-event input is larger. EUROCONTROL's cancellation cost for 250–400 seats is **3.3–4.8 times** the narrowbody figure ([EUROCONTROL](https://ansperformance.eu/economics/cba/standard-inputs/chapters/cancellation_cost.html)), and a GE90-115B performance overhaul runs about **$22M** excluding life-limited parts ([IBA, 2025](https://www.iba.aero/resources/articles/iba-engine-value-and-lease-rate-update/)). Value per engine is therefore higher, but this model does not quantify it.

For an MRO, the levers shift. The cancellation and AOG part of ΔC belongs to the airline customer unless turnaround or availability guarantees pass it through. The MRO keeps slot-aware induction planning, engineer time and records. Serving several operators spreads the fixed running cost.

## Running cost and attribution swing the answer most

Moving each driver to its conservative and then its upside value, with everything else held at base, ranks what matters. The base case sits on a knife-edge: **nine of the twenty drivers turn it positive on their own**.

| Driver (all else at base; base net −$39.5k) | Net at conservative value | Net at upside value | Swing |
|---|---|---|---|
| Running cost (infrastructure, FTE) | −$185.2k | +$51.9k | $237.1k |
| Attribution α | −$157.3k | +$76.1k | $233.4k |
| Cover days saved per removal (lever B) | −$106.3k | +$67.4k | $173.7k |
| Secondary damage (probability, cost) | −$72.8k | +$85.2k | $158.0k |
| Cost per false inspection | −$128.4k | +$5.7k | $134.1k |
| Coverage s | −$104.5k | +$25.4k | $129.9k |
| Short-notice lease (days, premium) | −$77.0k | +$41.5k | $118.5k |
| Cost per early pull | −$123.0k | −$9.0k | $114.0k |
| Removal rates (UER, SVR) | −$53.5k | +$50.8k | $104.3k |
| Cancellations per event | −$54.7k | +$27.8k | $82.6k |
| Detection TPR | −$75.6k | −$3.5k | $72.2k |
| Share of early pulls acted on | −$86.0k | −$16.3k | $69.8k |

The smallest drivers are emergency logistics ($41.6k swing), share of false inspections acted on ($37.1k), compliance ($28.5k), the false-inspection rate ($27.8k), AOG days ($27.3k), engineer time ($26.5k), the early-pull rate ($23.3k) and green time ($21.2k).

The ranking says three things. First, **the case depends on running cheaply and on catching what monitoring misses, not on saving engineer time**, which is the second-least important driver. Second, **the break-even thresholds sit just above the base values**, so modest evidence from a trial would move the verdict either way:

| Break-even threshold | Excluding integration | Including integration (spread over 5 years) | Base estimate |
|---|---|---|---|
| Attribution α | 0.37 | 0.47 | 0.30 |
| Extra conversions a year | 1.13 (6.1% of unscheduled removals) | 1.58 (8.5%) | 0.83 |

Third, **one rare event outweighs years of cost**. A single avoided $1.0M HPT blade replacement pays about **eight years** of base running cost, and removing secondary damage altogether moves the base net from −$40k to −$81k. Two stress tests show how false calls bite: doubling both false-alarm rates gives −$142k, and acting on every false call gives −$105k.

The three scenarios are corners in which all 36 uncertain inputs move together, which is unlikely. A Monte Carlo run drew 20,000 combinations, sampling each input independently from a triangular distribution between its conservative and upside values with its mode at base. Steady-state net came out at **P10 −$118k, median +$12k, P90 +$183k**, with a mean of +$24k and a **54% chance of being positive**. Spreading integration over five years, the median is −$58k and the chance of a positive result falls to 32%. Independence understates correlated risk: a LEAP/GTF-heavy fleet raises removal rates, lease premiums and secondary-damage costs at the same time. The honest reading is a result centred near zero, with a long right tail that only field evidence can confirm.

**Cross-checks place the per-event values in a credible range.** Koops's independent model finds **$12.3–92.6k net benefit per widebody engine per removal interval** for one failure mode, measured against no prediction and before overhead ([Koops, 2018](https://www.bauhaus-luftfahrt.net/fileadmin/user_upload/Publikationen/2018_Koops_Business_Case_Analysis_Predictive_Maintenance_PHME.pdf)). At this fleet's 3,300 hours a year, Koops's 31,250-hour interval lasts 9.5 years, so that is **$1.3–9.8k per engine-year**. With attribution set to 1 to match Koops's no-prediction baseline, this model's base gross is $583k, or **$2.9k per engine-year**, inside his range.

easyJet reports that Skywise helped it avoid **1,343 cancellations between January 2019 and September 2025** across about 350 aircraft ([ePlane AI](https://www.eplaneai.com/news/easyjet-highlights-reliability-improvements-ahead-of-pam-dublin-2025)). Priced at EUROCONTROL's €16,640–25,720 per cancellation, that implies **$9.9–15.4k per aircraft-year**, across all aircraft systems and with no attribution haircut. Cleris's base gross of $1.9k per aircraft looks proportionate for an engine-only, incremental tool. Its upside of $28k per aircraft exceeds easyJet's whole programme, which marks it as a ceiling rather than a forecast. Finally, a DLR lifecycle study found that even perfect monitoring of one aircraft system lifts lifecycle NPV by **at most 0.48%** ([Hölzel et al., ICAS 2012](https://icas.org/ICAS_ARCHIVE/ICAS2012/PAPERS/791.PDF)). Modest values are the norm for this kind of tool, not a sign that this model is pessimistic.

## Measured, sourced and assumed numbers are three different things

The model follows Cleris's own rule, no number without evidence, and that rule shows how little of the dollar result is measured. Cleris's measurements are all safety and conduct metrics on simulated data: 45 of 45 engines failing within 15 days sent for removal, swap or repair; 41 inspections and 1 early pull among 102 healthy engines; 1 of 53 engines in the 15–40 day band told to keep flying; a planning bound at or below true remaining life for 92% of engines; 3,401 of 3,401 claims citing evidence that resolves; 357 of 357 seeded faulty briefs caught; and a median run time of 0.38 seconds ([Cleris evaluation report](../evaluation/report.md)). The telemetry is NASA's simulated C-MAPSS dataset, and all maintenance, spares and document data is synthetic ([README](../README.md)). Neither the repo nor the pitch material contains any measurement of time saved, removals avoided or cost.

| Category | What it covers in this model | Examples |
|---|---|---|
| **Measured in Cleris** (simulated, synthetic data) | Detection on simulated engines, false-call rates, evidence and gate behaviour | 45/45 near-failure engines actioned; 41/102 inspections; 1/102 early pull; 1/53 kept flying in the 15–40 day band; 92% planning bound; 8/8 dropped-mandate faults caught |
| **Sourced from industry** | Unit costs and base rates | EUROCONTROL cancellation costs; IBA, ISTAT and Cirium lease rates; FAA borescope ($340) and HPT replacement ($1.0M) estimates; Koops removal rates and contingency damage; Aircraft Commerce reserves per cycle; BLS wages; FAA penalties; Feldman infrastructure baseline; GE notification rate |
| **Sourced, lower quality** | Unit costs with no published method | Air Cargo Week logistics and removal-and-installation costs; Safe Fly shop-visit costs |
| **Assumed for this model** | Every frequency and share that turns unit costs into annual dollars | Coverage, real-world detection and attribution; cancellations, AOG and lease days per event; secondary-damage probability; cover days saved; hours per decision; AD-lapse probability; share of decisions near removal; share of false calls acted on; running FTE; integration cost; adoption ramp; 1.05 USD/EUR |

Every dollar of value in the model therefore depends on at least one assumption. The ones that matter most (attribution, coverage, cover days and secondary-damage probability) can only be settled by running Cleris alongside an operator's existing process. Better literature will not settle them.

## Conclusion

Cleris's economics are a tail bet with a measurable trigger. Most of the dollars sit in a few high-consequence events: secondary damage, multi-day AOG, and spare engines bought at spot rates. These come from removals that today's monitoring would have caught too late to plan. A 12-month shadow trial, running Cleris alongside the existing process and counting two things, would collapse most of this model's uncertainty. The first count is extra planned conversions. The second is false calls that engineers acted on. If the first count reaches one or two a year for a 100-aircraft fleet while the second stays low, the business case holds. Engineer time, the most intuitive lever, cannot carry the case at any plausible value.

The model also shows that Cleris's caution is tuned for simulation, not for a real fleet. On C-MAPSS, where every engine is degrading, a 40% inspection rate on "healthy" engines is cheap insurance. In a real fleet, the same rates would produce nearly as many early-pull calls as genuine extra catches, and monthly sweeps would bury engineers in borescope requests. The next engineering milestone with economic weight is therefore precision at realistic prevalence, not recall. Because running cost is largely fixed, the first customers to show positive value are likely to be larger fleets, or MROs serving several operators.
