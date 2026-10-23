"""Structured data for the agents. Live: read-only SQL through Zetaris. Snapshot: small Parquet extracts in
data/snapshot (sample mode, tests, judging without credentials). FallbackSource switches to the snapshot for
the rest of the run when Zetaris fails, and every result carries the reason."""

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx
import pandas as pd

from data.fleet import SENSORS
from settings import ROOT, Settings

SNAPSHOT_DIR = ROOT / "data" / "snapshot"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

CYCLE_COLUMNS = ["asset_id", "cycle", "ts", "operating_regime", "setting_1", "setting_2", "setting_3", *SENSORS]
ASSET_COLUMNS = ["asset_id", "asset_type", "dataset", "esn", "fleet_status", "last_cycle", "last_seen"]
HISTORY_COLUMNS = ["wo_id", "wo_type", "cycle_at", "opened_at", "component", "description", "severity", "failure_mode", "note"]
PART_COLUMNS = ["part_number", "description", "component", "lead_time_days", "unit_cost_usd", "qty_on_hand", "qty_reserved"]
SLOT_COLUMNS = ["slot_date", "slot_no"]
SCHEDULE_COLUMNS = ["flight_date", "planned_cycles"]
FMEA_COLUMNS = ["fmea_id", "failure_mode", "task", "task_type", "trigger_severity", "part_number", "qty",
                "qty_on_hand", "qty_reserved", "lead_time_days"]
DOC_COLUMNS = ["chunk_id", "doc_id", "doc_type", "title", "revision", "effective_date", "superseded_by",
               "superseded_on", "asset_type", "applies_to", "section", "heading", "components", "failure_modes",
               "tasks", "topic", "mandate", "text", "embedding"]

INT_COLUMNS = {"cycle", "last_cycle", "cycle_at", "lead_time_days", "qty_on_hand", "qty_reserved", "slot_no",
               "planned_cycles", "qty"}
FLOAT_COLUMNS = {"setting_1", "setting_2", "setting_3", "unit_cost_usd", *SENSORS}
TIME_COLUMNS = {"ts", "last_seen", "opened_at"}
DATE_COLUMNS = {"slot_date", "flight_date"}


def safe_id(value: str) -> str:
    if not SAFE_ID.match(value or ""):
        raise ValueError(f"invalid identifier {value!r}")
    return value


def coerce(frame: pd.DataFrame) -> pd.DataFrame:
    """Zetaris returns strings; the snapshot returns typed columns. Both leave here with the same dtypes."""
    frame = frame.copy()
    for column in frame.columns:
        if column in INT_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
        elif column in FLOAT_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
        elif column in TIME_COLUMNS:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
        elif column in DATE_COLUMNS:
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.date
        else:
            frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    return frame


@dataclass
class QueryResult:
    frame: pd.DataFrame
    source: str
    query: str | None = None
    duration_s: float = 0.0
    degraded: str | None = None


class DataSource(ABC):
    mode: str

    @abstractmethod
    def asset(self, asset_id: str) -> QueryResult: ...

    @abstractmethod
    def cycles(self, asset_id: str) -> QueryResult: ...

    @abstractmethod
    def history(self, asset_id: str) -> QueryResult: ...

    @abstractmethod
    def parts(self, part_numbers: list[str]) -> QueryResult: ...

    @abstractmethod
    def free_slots(self) -> QueryResult: ...

    @abstractmethod
    def flight_schedule(self, asset_id: str) -> QueryResult: ...

    @abstractmethod
    def fmea_tasks(self, failure_mode: str) -> QueryResult: ...

    @abstractmethod
    def doc_chunks(self, asset_type: str) -> QueryResult: ...


class ZetarisSource(DataSource):
    mode = "live"

    def __init__(self, settings: Settings):
        from tools.zetaris import ZetarisClient

        self._client = ZetarisClient(settings, timeout_s=settings.zetaris_timeout_s)

    def _run(self, source: str, sql: str, columns: list[str]) -> QueryResult:
        from tools.zetaris import rows_to_frame

        start = time.monotonic()
        frame = rows_to_frame(self._client.run_select(sql))
        frame = frame.reindex(columns=columns) if not frame.empty else pd.DataFrame(columns=columns)
        return QueryResult(coerce(frame), f"zetaris:{source}", sql, round(time.monotonic() - start, 2))

    def asset(self, asset_id: str) -> QueryResult:
        a = safe_id(asset_id)
        return self._run("cmms_pg.assets", f"SELECT {', '.join(ASSET_COLUMNS)} FROM cmms_pg.assets "
                         f"WHERE asset_id = '{a}' OR esn = '{a}'", ASSET_COLUMNS)

    def cycles(self, asset_id: str) -> QueryResult:
        a = safe_id(asset_id)
        return self._run("telemetry_pg.cycles", f"SELECT {', '.join(CYCLE_COLUMNS)} FROM telemetry_pg.cycles "
                         f"WHERE asset_id = '{a}' ORDER BY cycle", CYCLE_COLUMNS)

    def history(self, asset_id: str) -> QueryResult:
        a = safe_id(asset_id)
        sql = ("SELECT w.wo_id, w.wo_type, w.cycle_at, w.opened_at, w.component, w.description, "
               "f.severity, f.failure_mode, f.note FROM cmms_pg.work_orders w "
               f"LEFT JOIN cmms_pg.findings f ON f.wo_id = w.wo_id WHERE w.asset_id = '{a}' ORDER BY w.cycle_at, w.wo_id")
        return self._run("cmms_pg.work_orders+findings", sql, HISTORY_COLUMNS)

    def parts(self, part_numbers: list[str]) -> QueryResult:
        numbers = ", ".join(f"'{safe_id(p)}'" for p in sorted(set(part_numbers))) or "''"
        sql = ("SELECT p.part_number, p.description, p.component, p.lead_time_days, p.unit_cost_usd, "
               "s.qty_on_hand, s.qty_reserved FROM cmms_pg.planning_parts p "
               f"LEFT JOIN cmms_pg.planning_stock s ON s.part_number = p.part_number WHERE p.part_number IN ({numbers}) "
               "ORDER BY p.part_number")
        return self._run("cmms_pg.planning_parts+planning_stock", sql, PART_COLUMNS)

    def free_slots(self) -> QueryResult:
        sql = ("SELECT slot_date, slot_no FROM cmms_pg.planning_hangar_slots WHERE status = 'free' "
               "ORDER BY slot_date, slot_no")
        return self._run("cmms_pg.planning_hangar_slots", sql, SLOT_COLUMNS)

    def flight_schedule(self, asset_id: str) -> QueryResult:
        a = safe_id(asset_id)
        sql = (f"SELECT flight_date, planned_cycles FROM cmms_pg.planning_flight_schedule WHERE asset_id = '{a}' "
               "ORDER BY flight_date")
        return self._run("cmms_pg.planning_flight_schedule", sql, SCHEDULE_COLUMNS)

    def fmea_tasks(self, failure_mode: str) -> QueryResult:
        m = safe_id(failure_mode)
        sql = ("SELECT f.fmea_id, f.failure_mode, f.task, f.task_type, f.trigger_severity, f.part_number, f.qty, "
               "s.qty_on_hand, s.qty_reserved, p.lead_time_days FROM s3_files.fmea f "
               "LEFT JOIN cmms_pg.planning_stock s ON s.part_number = f.part_number "
               "LEFT JOIN cmms_pg.planning_parts p ON p.part_number = f.part_number "
               f"WHERE f.failure_mode = '{m}' ORDER BY f.fmea_id, f.part_number")
        return self._run("s3_files.fmea+cmms_pg.planning_stock", sql, FMEA_COLUMNS)

    def doc_chunks(self, asset_type: str) -> QueryResult:
        t = safe_id(asset_type)
        sql = (f"SELECT {', '.join(DOC_COLUMNS)} FROM knowledge_pg.doc_chunks WHERE asset_type = '{t}' "
               "ORDER BY chunk_id")
        return self._run("knowledge_pg.doc_chunks", sql, DOC_COLUMNS)

    def close(self) -> None:
        self._client.close()


class SnapshotSource(DataSource):
    mode = "snapshot"

    def __init__(self, directory: Path = SNAPSHOT_DIR):
        if not (directory / "cycles.parquet").exists():
            raise FileNotFoundError(f"no snapshot in {directory}; run: uv run task snapshot")
        self._dir = directory
        self._cache: dict[str, pd.DataFrame] = {}

    def _table(self, name: str) -> pd.DataFrame:
        if name not in self._cache:
            self._cache[name] = coerce(pd.read_parquet(self._dir / f"{name}.parquet"))
        return self._cache[name]

    def _result(self, name: str, frame: pd.DataFrame, columns: list[str]) -> QueryResult:
        return QueryResult(frame[columns].reset_index(drop=True), f"snapshot:{name}.parquet")

    def asset(self, asset_id: str) -> QueryResult:
        a = safe_id(asset_id)
        t = self._table("assets")
        return self._result("assets", t[(t.asset_id == a) | (t.esn == a)], ASSET_COLUMNS)

    def cycles(self, asset_id: str) -> QueryResult:
        t = self._table("cycles")
        return self._result("cycles", t[t.asset_id == safe_id(asset_id)].sort_values("cycle"), CYCLE_COLUMNS)

    def history(self, asset_id: str) -> QueryResult:
        t = self._table("history")
        rows = t[t.asset_id == safe_id(asset_id)].sort_values(["cycle_at", "wo_id"])
        return self._result("history", rows, HISTORY_COLUMNS)

    def parts(self, part_numbers: list[str]) -> QueryResult:
        t = self._table("parts")
        wanted = {safe_id(p) for p in part_numbers}
        return self._result("parts", t[t.part_number.isin(wanted)].sort_values("part_number"), PART_COLUMNS)

    def free_slots(self) -> QueryResult:
        t = self._table("hangar_slots")
        return self._result("hangar_slots", t.sort_values(["slot_date", "slot_no"]), SLOT_COLUMNS)

    def flight_schedule(self, asset_id: str) -> QueryResult:
        t = self._table("flight_schedule")
        rows = t[t.asset_id == safe_id(asset_id)].sort_values("flight_date")
        return self._result("flight_schedule", rows, SCHEDULE_COLUMNS)

    def fmea_tasks(self, failure_mode: str) -> QueryResult:
        t = self._table("fmea_tasks")
        rows = t[t.failure_mode == safe_id(failure_mode)].sort_values(["fmea_id", "part_number"], na_position="first")
        return self._result("fmea_tasks", rows, FMEA_COLUMNS)

    def doc_chunks(self, asset_type: str) -> QueryResult:
        t = self._table("doc_chunks")
        rows = t[t.asset_type == safe_id(asset_type)].sort_values("chunk_id")
        return self._result("doc_chunks", rows, DOC_COLUMNS)


class FallbackSource(DataSource):
    """Live first. On the first Zetaris failure, or once live queries have used the run's time budget,
    every later call in the run uses the snapshot."""

    def __init__(self, live: DataSource, snapshot: DataSource, budget_s: float | None = None):
        self.live, self.snapshot = live, snapshot
        self.degraded: str | None = None
        self.budget_s, self.spent_s = budget_s, 0.0

    @property
    def mode(self) -> str:
        return "snapshot" if self.degraded else "live"

    def _call(self, name: str, *args) -> QueryResult:
        from tools.zetaris import ZetarisError

        if self.degraded is None and self.budget_s is not None and self.spent_s >= self.budget_s:
            self.degraded = (f"Zetaris slow ({self.spent_s:.0f} s of the {self.budget_s:.0f} s query budget used); "
                             "using snapshot")
        if self.degraded is None:
            start = time.monotonic()
            try:
                return getattr(self.live, name)(*args)
            except (ZetarisError, httpx.HTTPError) as error:
                self.degraded = f"Zetaris unavailable ({type(error).__name__}: {str(error)[:160]}); using snapshot"
            finally:
                self.spent_s += time.monotonic() - start
        result = getattr(self.snapshot, name)(*args)
        result.degraded = self.degraded
        return result

    def asset(self, asset_id): return self._call("asset", asset_id)
    def cycles(self, asset_id): return self._call("cycles", asset_id)
    def history(self, asset_id): return self._call("history", asset_id)
    def parts(self, part_numbers): return self._call("parts", part_numbers)
    def free_slots(self): return self._call("free_slots")
    def flight_schedule(self, asset_id): return self._call("flight_schedule", asset_id)
    def fmea_tasks(self, failure_mode): return self._call("fmea_tasks", failure_mode)
    def doc_chunks(self, asset_type): return self._call("doc_chunks", asset_type)


def open_data_source(settings: Settings) -> DataSource:
    from tools.zetaris import ZetarisError

    snapshot = SnapshotSource()
    if settings.sample_mode or settings.zetaris_mode == "snapshot":
        return snapshot
    try:
        return FallbackSource(ZetarisSource(settings), snapshot, budget_s=settings.zetaris_budget_s)
    except ZetarisError as error:
        fallback = FallbackSource(snapshot, snapshot)
        fallback.degraded = f"Zetaris not configured ({error}); using snapshot"
        return fallback
