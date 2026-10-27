"""The brief shows the SQL a Zetaris evidence record ran. Display only: nothing here changes a number or a decision."""

from assurance.sql_guard import guard_select
from ui.brief import zetaris_sql


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
