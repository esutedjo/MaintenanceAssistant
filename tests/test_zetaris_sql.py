"""The brief shows the SQL a Zetaris evidence record ran, and can re-run it through Zetaris. Display only:
nothing here changes a number or a decision."""

import copy

from assurance.sql_guard import guard_select
from ui.brief import ZETARIS_RERUN_FAIL, rerun_zetaris, zetaris_sql


def _fmea_sql() -> str:
    """The statement ZetarisSource.fmea_tasks sends, captured without a client."""
    from tools.data_access import ZetarisSource

    class Capture(ZetarisSource):
        def __init__(self):
            self.sql = ""

        def _run(self, source, sql, columns):
            self.sql = sql

    capture = Capture()
    capture.fmea_tasks("hpc_degradation")
    return capture.sql


def test_fmea_join_lists_its_source_tables_and_stays_read_only():
    item = {"source": "zetaris:s3_files.fmea+cmms_pg.planning_stock",
            "content": {"query": _fmea_sql(), "duration_s": 1.25}}
    got = zetaris_sql(item)
    assert got is not None
    assert got["tables"] == ["s3_files.fmea", "cmms_pg.planning_stock", "cmms_pg.planning_parts"]
    assert got["duration_s"] == 1.25
    assert "\n" in got["sql"] and "s3_files.fmea" in got["sql"]
    guarded = guard_select(got["sql"], 1000)
    assert guarded.endswith("LIMIT 1000")
    for table in got["tables"]:
        assert table in guarded


def test_snapshot_record_returns_none():
    assert zetaris_sql({"source": "snapshot:fmea_tasks.parquet",
                        "content": {"query": None, "duration_s": 0.0}}) is None
    assert zetaris_sql({"source": "snapshot:cycles.parquet",
                        "content": {"query": _fmea_sql(), "duration_s": 0.4}}) is None
    assert zetaris_sql({"source": "model:rul_a@turbofan_cmapss/0.1.0", "content": {"rul": 9.8}}) is None


def _zetaris_item(**content) -> dict:
    body = {"query": _fmea_sql(), "duration_s": 1.25, "rows": 2}
    body.update(content)
    return {"source": "zetaris:s3_files.fmea+cmms_pg.planning_stock", "content": body}


def test_rerun_returns_rows_and_timing_through_the_select_guard():
    item = _zetaris_item()
    original = copy.deepcopy(item)
    seen = []

    def run_select(sql):
        seen.append(sql)
        guarded = guard_select(sql, 1000)
        assert guarded.endswith("LIMIT 1000")
        return {"data": [["FM-HPC-01", "P-HPC-01"], ["FM-HPC-06", "P-HPC-06"]],
                "headers": ["fmea_id", "part_number"]}

    got = rerun_zetaris(item, run_select)
    assert seen == [item["content"]["query"]]
    assert got["error"] is None
    assert got["row_count"] == 2
    assert list(got["rows"].columns) == ["fmea_id", "part_number"]
    assert len(got["rows"]) == 2
    assert isinstance(got["duration_s"], (int, float)) and not isinstance(got["duration_s"], bool)
    assert got["duration_s"] >= 0
    assert got["matches"] is True
    assert item == original


def test_rerun_refuses_snapshot_or_model_records_without_calling_zetaris():
    called = []

    def run_select(sql):
        called.append(sql)
        raise AssertionError("must not call Zetaris")

    snapshot = {"source": "snapshot:cycles.parquet", "content": {"query": _fmea_sql(), "rows": 180}}
    model = {"source": "model:rul_a@turbofan_cmapss/0.1.0", "content": {"rul": 9.8}}
    for item in (snapshot, model):
        original = copy.deepcopy(item)
        got = rerun_zetaris(item, run_select)
        assert got["error"] == "not a Zetaris record"
        assert got["rows"] is None and got["row_count"] is None
        assert item == original
    assert called == []


def test_failing_run_select_returns_the_error_and_leaves_the_item_unchanged():
    item = _zetaris_item(rows=16)
    original = copy.deepcopy(item)

    def run_select(sql):
        guard_select(sql, 1000)
        raise TimeoutError("stalled")

    got = rerun_zetaris(item, run_select)
    assert got["error"] == ZETARIS_RERUN_FAIL
    assert got["error"] == "Zetaris did not answer; the brief is unchanged."
    assert got["rows"] is None and got["row_count"] is None and got["matches"] is None
    assert item == original
