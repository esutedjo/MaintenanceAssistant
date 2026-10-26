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

## 72-31-00-601 HPC blade borescope limits (table 601)
<!-- {components: [hpc], topic: "limits:borescope:hpc_blades"} -->
SYNTHETIC LIMITS written for the hackathon demo; not OEM data, do not use for real maintenance. Measure the worst
HPC stage 1 to 9 blade through the borescope ports and compare it with the table. Within the serviceable limit the
blade stays in service and the next borescope follows the routine interval. Above the serviceable limit but not above
the reject limit (at the limit) the blade stays in service at a reduced interval: re-inspect and plan a module shop
visit (FM-HPC-03). Above the reject limit (beyond limits) remove the engine before further flight (FM-HPC-04).
Limit HPC-TIP: blade tip erosion, mm; serviceable up to 0.50; reject above 0.75.
Limit HPC-NICK: leading-edge nick depth, mm; serviceable up to 0.25; reject above 0.50.
Limit HPC-COAT: coating loss (share of airfoil area), %; serviceable up to 10; reject above 25.
Interval: within limits next inspection 60 cycles; at limit re-inspect within 30 cycles; beyond limits remove before
further flight.

## 72-21-00-601 Fan blade leading-edge damage limits (table 601)
<!-- {components: [fan], topic: "limits:visual:fan_blades"} -->
SYNTHETIC LIMITS written for the hackathon demo; not OEM data. Inspect the fan blade leading edges visually, turning
the fan by hand, and measure the deepest nick or dent and the erosion of the leading edge. Within the serviceable
limit, blend on wing if needed and keep the routine interval. At the limit, blend, re-inspect at the reduced interval
and plan the blade pair replacement (FM-FAN-02). Beyond limits, remove the engine before further flight (FM-FAN-03).
Limit FAN-NICK: leading-edge nick or dent depth, mm; serviceable up to 1.0; reject above 2.0.
Limit FAN-EROSION: leading-edge erosion (chord loss), mm; serviceable up to 1.5; reject above 3.0.
Interval: within limits next inspection 100 cycles; at limit re-inspect within 25 cycles; beyond limits remove before
further flight.

## 79-00-00-601 Spectrometric oil analysis wear-metal limits (table 601)
<!-- {components: [oil_system], topic: "limits:oil_analysis:wear_metals"} -->
SYNTHETIC LIMITS written for the hackathon demo; not OEM data. Take the oil sample within 30 minutes of shutdown and
compare each wear metal with the table. Rising iron (Fe) points at bearings and gears, chromium (Cr) at bearing races
and silver (Ag) at bearing cages. Within the serviceable limit, sample at the routine interval. At the limit,
resample at the reduced interval and check the magnetic chip detector. Beyond limits, remove the engine before
further flight for bearing and gearbox inspection.
Limit OIL-FE: iron (Fe), ppm; serviceable up to 4.0; reject above 8.0.
Limit OIL-CR: chromium (Cr), ppm; serviceable up to 1.0; reject above 2.0.
Limit OIL-AG: silver (Ag), ppm; serviceable up to 0.5; reject above 1.0.
Interval: within limits next inspection 25 cycles; at limit re-inspect within 5 cycles; beyond limits remove before
further flight.

## 79-30-00-601 Magnetic chip detector limits (table 601)
<!-- {components: [oil_system], topic: "limits:chip_detector:particles"} -->
SYNTHETIC LIMITS written for the hackathon demo; not OEM data. Remove the magnetic chip detector, measure the largest
particle and count the particles larger than a quarter of a millimetre. Fuzz and fine paste are normal wear. Within
the serviceable limit, clean and refit. At the limit, refit a clean detector, check again at the reduced interval
and send the debris for analysis. Beyond limits, remove the engine before further flight.
Limit CHIP-SIZE: largest particle, mm; serviceable up to 0.5; reject above 1.0.
Limit CHIP-COUNT: particles larger than 0.25 mm, count; serviceable up to 3; reject above 6.
Interval: within limits next inspection 50 cycles; at limit re-inspect within 10 cycles; beyond limits remove before
further flight.

## 72-00-00-601 Eddy-current crack indication limits (table 601)
<!-- {components: [fan, lpc, hpc, hpt, lpt], topic: "limits:material_test:crack_indication"} -->
SYNTHETIC LIMITS written for the hackathon demo; not OEM data. Applies to eddy-current and other NDT inspections of
blade roots, disk slots and cases. No reportable indication is serviceable. An indication up to the reject length is
below the method's confirmed-crack threshold: confirm it with a second method and re-inspect at the reduced interval.
A longer indication is a crack: remove the engine before further flight.
Limit NDT-CRACK: crack indication length, mm; serviceable up to 0.0; reject above 0.25.
Interval: within limits next inspection 100 cycles; at limit re-inspect within 10 cycles; beyond limits remove before
further flight.
