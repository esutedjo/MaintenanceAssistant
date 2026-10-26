import pytest

from assurance.sql_guard import UnsafeSQLError, guard_select


def test_adds_limit_when_missing():
    assert guard_select("SELECT unit, cycle FROM telemetry_pg.fd001", 100).endswith("LIMIT 100")


def test_keeps_smaller_limit():
    assert guard_select("SELECT * FROM t LIMIT 5", 100).endswith("LIMIT 5")


def test_caps_larger_limit():
    assert guard_select("SELECT * FROM t LIMIT 5000", 100).endswith("LIMIT 100")


def test_allows_cte_and_join():
    sql = "WITH w AS (SELECT unit FROM a) SELECT w.unit, b.part FROM w JOIN b ON w.unit = b.unit"
    assert "LIMIT 10" in guard_select(sql, 10)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM work_orders",
        "UPDATE spares SET stock = 0",
        "INSERT INTO t VALUES (1)",
        "DROP TABLE t",
        "CREATE TABLE t (a INT)",
        "SELECT 1; DROP TABLE t",
        "SHOW TABLES",
    ],
)
def test_rejects_non_select(sql):
    with pytest.raises(UnsafeSQLError):
        guard_select(sql, 100)


def test_rejects_unparseable():
    with pytest.raises(UnsafeSQLError):
        guard_select("SELECT FROM WHERE (", 100)


@pytest.mark.parametrize("sql", [
    "SELECT * INTO new_t FROM t",                                       # was re-emitted as CREATE TABLE AS SELECT
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
    "SELECT reflect('java.lang.Thread', 'sleep', 1000)",
    "SELECT java_method('java.lang.System', 'getProperty', 'user.home')",
    "SELECT pg_sleep(100)",
    "SELECT set_config('x', 'y', false)",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT * FROM t FOR UPDATE",
])
def test_rejects_into_side_effect_functions_and_locks(sql):
    with pytest.raises(UnsafeSQLError):
        guard_select(sql, 100)


def test_the_app_queries_still_pass():
    """Every statement tools/data_access.py and the status checks send."""
    from tools.data_access import ZetarisSource
    from tools.status import SOURCE_PROBES

    class Capture(ZetarisSource):
        def __init__(self):
            self.sql = []

        def _run(self, source, sql, columns):
            self.sql.append(sql)

    capture = Capture()
    for name, args in [("asset", ("FD001-A031",)), ("cycles", ("FD001-A031",)), ("history", ("FD001-A031",)),
                       ("parts", (["TF-HPC-1101"],)), ("free_slots", ()), ("flight_schedule", ("FD001-A031",)),
                       ("fmea_tasks", ("hpc_degradation",)), ("doc_chunks", ("turbofan_cmapss",))]:
        getattr(capture, name)(*args)
    for sql in [*capture.sql, *(probe for _, _, probe in SOURCE_PROBES)]:
        assert guard_select(sql, 1000).endswith("LIMIT 1000"), sql


def test_safe_id_takes_the_whole_value_only():
    from tools.data_access import safe_id

    assert safe_id("FD001-A031") == "FD001-A031"
    for bad in ("FD001-A031\n", "FD001-A031' OR '1'='1", "", "x" * 41):
        with pytest.raises(ValueError):
            safe_id(bad)
