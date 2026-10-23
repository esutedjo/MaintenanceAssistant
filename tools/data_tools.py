"""Data agent tools: query a DataSource and condense the result into one Evidence record each.
The LLM reads the summary and the compact content, never raw rows; numbers are computed here."""

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from casefile.schemas import Evidence, EvidenceLedger
from tools.data_access import DataSource, QueryResult
from tools.data_quality import check_cycles, check_history, check_parts, check_sensors, overall

BASELINE_CYCLES = 20
RECENT_CYCLES = 10
SLOPE_CYCLES = 30


class AssetNotFound(LookupError):
    pass


@dataclass
class Telemetry:
    evidence: Evidence
    cycles: pd.DataFrame


def _num(value, digits: int = 4):
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None
    if isinstance(value, (int, np.integer)):
        return int(value)
    return round(float(value), digits)


def _iso(value) -> str | None:
    if value is None or value is pd.NaT or (isinstance(value, float) and np.isnan(value)):
        return None
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _nanmean(values: np.ndarray) -> float:
    return float(np.nanmean(values)) if np.isfinite(values).any() else float("nan")


def _nanstd(values: np.ndarray) -> float:
    return float(np.nanstd(values)) if np.isfinite(values).any() else 0.0


def _available(row) -> int:
    """Stock that can be issued; a negative count is impossible (data_quality reports it) and counts as none."""
    return max(0, (_num(row.qty_on_hand) or 0) - (_num(row.qty_reserved) or 0))


class DataTools:
    def __init__(self, source: DataSource, ledger: EvidenceLedger, pack: dict, reference_time: datetime):
        self.source, self.ledger, self.pack = source, ledger, pack
        self.reference_time = reference_time
        self.cycles_per_day = pack["thresholds"]["cycles_per_day"]

    def _add(self, result: QueryResult, summary: str, content: dict, extra_degraded: str | None = None) -> Evidence:
        content = {**content, "query": result.query, "duration_s": result.duration_s}
        degraded = "; ".join(d for d in (result.degraded, extra_degraded) if d) or None
        return self.ledger.add("query", result.source, summary, content, degraded=degraded)

    def asset_profile(self, asset_id: str) -> Evidence:
        result = self.source.asset(asset_id)
        if result.frame.empty:
            raise AssetNotFound(f"no asset {asset_id!r} in {result.source}")
        row = result.frame.iloc[0]
        content = {k: _iso(v) if k == "last_seen" else _num(v) if k == "last_cycle" else v for k, v in row.items()}
        summary = (f"{row.asset_id} ({row.esn}, {row.dataset}) is {row.fleet_status}; last observed cycle "
                   f"{row.last_cycle} at {_iso(row.last_seen)}")
        return self._add(result, summary, content)

    def telemetry(self, asset_id: str) -> Telemetry:
        """Trend of each sensor the asset pack marks as degrading: early baseline, recent level, recent slope."""
        result = self.source.cycles(asset_id)
        frame = result.frame
        if frame.empty:
            raise AssetNotFound(f"no telemetry for {asset_id!r} in {result.source}")
        sensors = {}
        for sensor, spec in self.pack["sensors"].items():
            if all(d == "none" for d in spec["direction"].values()):
                continue
            values = frame[sensor].to_numpy(float)           # missing values are reported by data_quality
            baseline = _nanmean(values[:BASELINE_CYCLES])
            recent = _nanmean(values[-RECENT_CYCLES:])
            tail = frame.tail(SLOPE_CYCLES)[["cycle", sensor]].astype(float).dropna()
            slope = np.polyfit(tail["cycle"], tail[sensor], 1)[0] if len(tail) > 2 else 0.0
            spread = _nanstd(values[:BASELINE_CYCLES]) or 1e-9
            sensors[sensor] = {
                "name": spec["name"], "unit": spec["unit"], "component": spec["component"],
                "baseline": _num(baseline), "recent": _num(recent), "change": _num(recent - baseline),
                "change_sigma": _num((recent - baseline) / spread, 2), "slope_per_cycle": _num(slope, 6),
            }
        first, last = int(frame.cycle.min()), int(frame.cycle.max())
        moved = sorted(sensors, key=lambda s: -abs(sensors[s]["change_sigma"] or 0))[:3]
        moved_text = ", ".join(f"{sensors[s]['name']} {sensors[s]['change_sigma']:+.1f} sd" for s in moved)
        summary = (f"{asset_id}: {len(frame)} cycles ({first}-{last}); largest moves vs first {BASELINE_CYCLES} "
                   f"cycles: {moved_text}")
        content = {"asset_id": asset_id, "rows": len(frame), "first_cycle": first, "last_cycle": last,
                   "baseline_cycles": BASELINE_CYCLES, "recent_cycles": RECENT_CYCLES,
                   "slope_cycles": SLOPE_CYCLES, "sensors": sensors}
        return Telemetry(self._add(result, summary, content), frame)

    def data_quality(self, asset_id: str, cycles: pd.DataFrame, profile: dict | None = None) -> Evidence:
        """Plausibility of the inputs before any model reads them. Serious issues lower confidence and escalate."""
        profile = profile or {}
        last_cycle = profile.get("last_cycle")
        history = self.source.history(asset_id).frame
        issues = [*check_cycles(cycles, last_cycle, self.reference_time), *check_sensors(cycles, self.pack),
                  *check_history(history, last_cycle if last_cycle is not None else
                                 (int(cycles.cycle.max()) if len(cycles) else None),
                                 profile.get("fleet_status"), self.reference_time)]
        severity = overall(issues)
        checked = (f"{len(cycles)} cycles, {len(self.pack['sensors'])} sensors, {len(history)} work orders")
        if issues:
            summary = (f"{asset_id}: input data {severity}: " + "; ".join(i.detail for i in issues[:3])
                       + (f"; {len(issues) - 3} more" if len(issues) > 3 else ""))
        else:
            summary = f"{asset_id}: input data plausible ({checked} within asset-pack limits)"
        content = {"asset_id": asset_id, "data_quality": severity, "issues": [i.as_dict() for i in issues],
                   "checked": checked}
        degraded = f"Data quality {severity}: {issues[0].detail}" + (
            f" (+{len(issues) - 1} more)" if len(issues) > 1 else "") if issues else None
        return self.ledger.add("rule", f"asset_pack:{self.pack['asset_type']}/data_quality", summary, content,
                               degraded=degraded)

    def history(self, asset_id: str) -> Evidence:
        result = self.source.history(asset_id)
        frame = result.frame
        inspections = frame[frame.wo_type == "inspection"]
        findings = inspections[inspections.severity.notna() & (inspections.severity != "none")]
        last = inspections.iloc[-1] if len(inspections) else None
        content = {
            "asset_id": asset_id,
            "work_orders": len(frame),
            "by_type": {k: int(v) for k, v in frame.wo_type.value_counts().items()},
            "last_inspection": None if last is None else {
                "wo_id": last.wo_id, "cycle": _num(last.cycle_at), "date": _iso(last.opened_at),
                "severity": last.severity, "note": last.note},
            "findings": [{"wo_id": r.wo_id, "cycle": _num(r.cycle_at), "severity": r.severity,
                          "failure_mode": r.failure_mode, "note": r.note} for r in findings.itertuples()],
        }
        if last is None:
            summary = f"{asset_id}: {len(frame)} work orders, no inspections on record"
        else:
            summary = (f"{asset_id}: {len(frame)} work orders; last inspection {last.wo_id} at cycle {last.cycle_at} "
                       f"severity {last.severity}; {len(findings)} inspection(s) with findings")
        return self._add(result, summary, content)

    def parts(self, part_numbers: list[str]) -> Evidence:
        result = self.source.parts(part_numbers)
        rows = []
        for r in result.frame.itertuples():
            available = _available(r)
            rows.append({"part_number": r.part_number, "description": r.description, "component": r.component,
                         "available": available, "lead_time_days": _num(r.lead_time_days),
                         "unit_cost_usd": _num(r.unit_cost_usd, 2)})
        missing = sorted(set(part_numbers) - {r["part_number"] for r in rows})
        issues = check_parts([{"part_number": r.part_number, "qty_on_hand": _num(r.qty_on_hand),
                               "qty_reserved": _num(r.qty_reserved), "lead_time_days": _num(r.lead_time_days)}
                              for r in result.frame.itertuples()])
        summary = "; ".join(
            f"{r['part_number']} ({r['description']}): {r['available']} available, lead time {r['lead_time_days']} d"
            for r in rows) or "no matching parts"
        if missing:
            summary += f"; unknown part numbers: {', '.join(missing)}"
        if issues:
            summary += "; implausible stock data: " + "; ".join(i.detail for i in issues)
        note = ("Data quality serious: " + "; ".join(i.detail for i in issues)) if issues else None
        return self._add(result, summary, {"parts": rows, "unknown": missing, "issues": [i.as_dict() for i in issues]},
                         extra_degraded=note)

    def free_slots(self, show: int = 5) -> Evidence:
        result = self.source.free_slots()
        today = self.reference_time.date()
        slots = [d for d in result.frame.slot_date if d >= today]
        first = slots[0] if slots else None
        days = (first - today).days if first else None
        content = {"reference_date": today.isoformat(), "free_slots": len(slots),
                   "first_free": _iso(first), "days_to_first_free": days,
                   "next": [_iso(d) for d in slots[:show]]}
        summary = (f"{len(slots)} free hangar slots; first on {first} ({days} days from {today})" if first
                   else f"no free hangar slots after {today}")
        return self._add(result, summary, content)

    def flight_schedule(self, asset_id: str) -> Evidence:
        result = self.source.flight_schedule(asset_id)
        frame = result.frame
        today = self.reference_time.date()
        upcoming = frame[frame.flight_date >= today]
        days = (upcoming.flight_date - today).map(lambda d: d.days)
        content = {"asset_id": asset_id, "days_scheduled": len(upcoming),
                   "cycles_next_7_days": int(upcoming.planned_cycles[days < 7].sum()),
                   "cycles_next_30_days": int(upcoming.planned_cycles[days < 30].sum()),
                   "last_scheduled": _iso(upcoming.flight_date.max()) if len(upcoming) else None}
        summary = (f"{asset_id}: {content['cycles_next_7_days']} cycles planned in 7 days, "
                   f"{content['cycles_next_30_days']} in 30 days")
        return self._add(result, summary, content)

    def fmea_tasks(self, failure_mode: str) -> Evidence:
        result = self.source.fmea_tasks(failure_mode)
        tasks: dict[str, dict] = {}
        for r in result.frame.itertuples():
            task = tasks.setdefault(r.fmea_id, {"fmea_id": r.fmea_id, "task": r.task, "task_type": r.task_type,
                                                "trigger_severity": r.trigger_severity, "parts": []})
            if r.part_number:
                available = _available(r)
                task["parts"].append({"part_number": r.part_number, "qty": _num(r.qty), "available": available,
                                      "lead_time_days": _num(r.lead_time_days)})
        for task in tasks.values():
            short = [p for p in task["parts"] if p["available"] < (p["qty"] or 1)]
            task["parts_short"] = [p["part_number"] for p in short]
            task["max_lead_time_days"] = max((p["lead_time_days"] or 0 for p in short), default=0)
        summary = f"{failure_mode}: {len(tasks)} FMEA tasks; " + "; ".join(
            f"{t['fmea_id']} {t['task_type']} ({t['trigger_severity']})"
            + (f", short: {', '.join(t['parts_short'])}" if t["parts_short"] else "") for t in tasks.values())
        return self._add(result, summary, {"failure_mode": failure_mode, "tasks": list(tasks.values())})
