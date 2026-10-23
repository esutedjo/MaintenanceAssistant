---
doc_id: AMM-TF-R3
doc_type: manual
title: Engine Maintenance Manual, C-MAPSS two-spool turbofan (SYNTHETIC)
revision: "3"
effective_date: 2026-03-01
asset_type: turbofan_cmapss
applies_to: [FD001, FD003]
---

SYNTHETIC DOCUMENT written for the hackathon demo. Not an OEM manual; do not use for real maintenance.

## 05-20-00 Gas-path trend monitoring
<!-- {components: [engine, hpc, fan], failure_modes: [hpc_degradation, fan_degradation]} -->
Trend every flight cycle against the engine's own baseline taken over its first 20 cycles after installation.
Rising T30, T50, core speed (Nc) and static pressure Ps30 at constant thrust indicate compressor efficiency
loss. Fan-side wear shows as falling bypass ratio (BPR) with rising P30 and fuel-flow ratio (phi); HPC-side
wear shows falling P30 and phi. When two independent remaining-life estimates differ by more than the
operator's divergence limit, do not average them: perform a borescope inspection of the suspected module
before the next planned maintenance decision.

## 72-21-00 Fan blades: inspection and blend limits
<!-- {components: [fan], failure_modes: [fan_degradation], tasks: [FM-FAN-01, FM-FAN-02, FM-FAN-03]} -->
Inspect fan blade leading edges and the fan case abradable liner visually at each A-check and whenever the
trend monitor indicates fan-side wear (FM-FAN-01). Nicks and erosion within blend limits may be blended on
wing. When erosion approaches blend limits, replace the worst blades as moment-weighted pairs (TF-FAN-2102)
and renew platform seals (TF-FAN-2104) (FM-FAN-02). Erosion beyond blend limits requires engine removal and
replacement of the fan blade set (TF-FAN-2101) and abradable liner (TF-FAN-2103) (FM-FAN-03).

## 72-31-00 HPC borescope inspection
<!-- {components: [hpc], failure_modes: [hpc_degradation], tasks: [FM-HPC-01, FM-HPC-03], topic: hpc_borescope_interval} -->
Borescope HPC stages 1 to 9 through the borescope ports using plug kit TF-GEN-8101 (FM-HPC-01). Routine
interval: every 60 cycles. When blade tip erosion or leading-edge loss approaches the limits in table 601,
reduce the interval to 30 cycles and plan a module shop visit (FM-HPC-03). Findings beyond limits on stages 1
to 3 require engine removal and replacement of the stage 1-3 blade set (TF-HPC-1101) and case abradable seals
(TF-HPC-1104) (FM-HPC-04).

## 72-31-10 On-wing compressor water wash
<!-- {components: [hpc], failure_modes: [hpc_degradation], tasks: [FM-HPC-02]} -->
A water wash recovers performance lost to fouling only. It does not recover erosion or tip clearance growth.
Do not use a water wash to defer a borescope inspection called for by trend monitoring (FM-HPC-02).

## 71-00-00 Engine removal and lease engine installation
<!-- {components: [engine], failure_modes: [hpc_degradation, fan_degradation], tasks: [FM-HPC-06, FM-FAN-04]} -->
When the remaining-life lower bound is shorter than the parts lead time plus the wait for a hangar slot,
install a lease engine (TF-ENG-9001) so the aircraft stays in service while the removed engine waits for
parts. Ship the removed engine with its trend data and last borescope record. Renew gaskets and O-rings
(TF-GEN-8102) on installation.

## 72-50-00 HPT blade inspection
<!-- {components: [hpt], failure_modes: []} -->
Inspect high-pressure turbine stage 1 blades for coating loss, cracks and blocked cooling holes at each shop
visit. Rising HPT coolant bleed (W31) alone is not a removal criterion.
