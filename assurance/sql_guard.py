import sqlglot
from sqlglot import exp

_FORBIDDEN = tuple(
    getattr(exp, name)
    for name in ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "Command", "Into",
                 "Lock", "Set", "Use", "Copy", "Grant", "TruncateTable")
    if hasattr(exp, name)
)
# Functions with side effects or server access (file reads, remote links, sleeps, settings, reflection). No app SQL
# uses them; the read-only Zetaris role is the real control, this is defence in depth.
DENIED_FUNCTIONS = {
    "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR", "PG_STAT_FILE", "LO_EXPORT", "LO_IMPORT", "DBLINK",
    "DBLINK_EXEC", "DBLINK_CONNECT", "PG_SLEEP", "PG_SLEEP_FOR", "PG_SLEEP_UNTIL", "SET_CONFIG", "REFLECT",
    "JAVA_METHOD", "COPY", "PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND", "PG_RELOAD_CONF", "QUERY_TO_XML", "SLEEP",
}


class UnsafeSQLError(ValueError):
    pass


def _function_names(statement: exp.Expression) -> set[str]:
    names = set()
    for func in statement.find_all(exp.Func):
        if isinstance(func, exp.Anonymous):
            names.add(str(func.name).upper())
        else:
            names.add(func.sql_name().upper())
    return names


def _check(statement: exp.Expression) -> None:
    if not isinstance(statement, exp.Query):
        raise UnsafeSQLError(f"Only SELECT queries are allowed, got {statement.key.upper()}.")
    if any(statement.find(kind) for kind in _FORBIDDEN):
        raise UnsafeSQLError("Query contains a write, DDL, INTO or command clause.")
    denied = _function_names(statement) & DENIED_FUNCTIONS
    if denied:
        raise UnsafeSQLError(f"Query calls a function that is not allowed: {', '.join(sorted(denied))}.")


def guard_select(sql: str, max_rows: int, dialect: str = "spark") -> str:
    """Return the statement with a LIMIT of at most max_rows, or raise if it is not a single read-only query. The
    statement the guard returns is parsed and checked again, so a rewrite (SELECT ... INTO becoming CREATE TABLE AS)
    can never slip through."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as error:
        raise UnsafeSQLError(f"SQL does not parse: {error}") from error

    if len(statements) != 1:
        raise UnsafeSQLError(f"Exactly one statement is allowed, got {len(statements)}.")
    statement = statements[0]
    _check(statement)

    current = None
    limit = statement.args.get("limit")
    if limit is not None:
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int:
            current = int(value.this)
    if current is None or current > max_rows:
        statement = statement.limit(max_rows, copy=False)

    out = statement.sql(dialect=dialect)
    try:
        emitted = [s for s in sqlglot.parse(out, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as error:
        raise UnsafeSQLError(f"the guarded SQL does not parse: {error}") from error
    if len(emitted) != 1 or not isinstance(emitted[0], (exp.Select, exp.Union)):
        raise UnsafeSQLError("the guarded SQL is not a single SELECT.")
    _check(emitted[0])
    return out
