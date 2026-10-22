"""Read-only connectivity check against RDS and Zetaris. Run on the developer machine: uv run task check"""

import sys

import psycopg

from settings import get_settings
from tools.zetaris import ZetarisClient


def _check_postgres(label: str, dbname: str, user: str, password: str) -> bool:
    settings = get_settings()
    if not user or not password:
        print(f"SKIP {label}: user or password not set")
        return True
    try:
        with psycopg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            dbname=dbname,
            user=user,
            password=password,
            sslmode=settings.pg_sslmode,
            connect_timeout=10,
        ) as conn:
            conn.read_only = True
            row = conn.execute("SELECT current_user, current_database(), version()").fetchone()
        print(f"PASS {label}: {row[0]}@{row[1]} ({row[2].split(',')[0]})")
        return True
    except psycopg.Error as error:
        print(f"FAIL {label}: {type(error).__name__}: {str(error).strip().splitlines()[0]}")
        return False


def _check_zetaris(sql: str) -> bool:
    try:
        client = ZetarisClient(get_settings())
        try:
            result = client.run_select(sql)
        finally:
            client.close()
    except Exception as error:
        print(f"FAIL zetaris [{sql}]: {type(error).__name__}: {error}")
        return False
    shape = sorted(result) if isinstance(result, dict) else type(result).__name__
    print(f"PASS zetaris [{sql}]: response keys {shape}")
    return True


def main() -> int:
    settings = get_settings()
    loader_pw = settings.pg_loader_password.get_secret_value()
    app_pw = settings.pg_app_password.get_secret_value()
    results = [
        _check_postgres("rds telemetry (loader)", settings.pg_db_telemetry, settings.pg_loader_user, loader_pw),
        _check_postgres("rds knowledge (loader)", settings.pg_db_knowledge, settings.pg_loader_user, loader_pw),
        _check_postgres("rds cmms (app_rw)", settings.pg_db_cmms, settings.pg_app_user, app_pw),
        _check_zetaris("SELECT 1 AS ok"),
        _check_zetaris("SELECT COUNT(*) AS n FROM telemetry_pg.smoke"),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
