import sqlglot
from sqlglot import exp

_FORBIDDEN = tuple(
    getattr(exp, name)
    for name in ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "Command")
    if hasattr(exp, name)
)


class UnsafeSQLError(ValueError):
    pass


def guard_select(sql: str, max_rows: int, dialect: str = "spark") -> str:
    """Return the statement with a LIMIT of at most max_rows, or raise if it is not a single read-only query."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as error:
        raise UnsafeSQLError(f"SQL does not parse: {error}") from error

    if len(statements) != 1:
        raise UnsafeSQLError(f"Exactly one statement is allowed, got {len(statements)}.")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise UnsafeSQLError(f"Only SELECT queries are allowed, got {statement.key.upper()}.")
    if any(statement.find(kind) for kind in _FORBIDDEN):
        raise UnsafeSQLError("Query contains a write or DDL clause.")

    current = None
    limit = statement.args.get("limit")
    if limit is not None:
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int:
            current = int(value.this)
    if current is None or current > max_rows:
        statement = statement.limit(max_rows, copy=False)

    return statement.sql(dialect=dialect)
