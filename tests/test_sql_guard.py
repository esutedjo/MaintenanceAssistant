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
