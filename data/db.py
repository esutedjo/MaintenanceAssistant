from pathlib import Path
from typing import Literal

import psycopg
import yaml

from settings import ROOT, get_settings

Target = Literal["rds", "local"]
PACK_DIR = ROOT / "asset_packs" / "turbofan_cmapss"


def load_pack(pack_dir: Path = PACK_DIR) -> dict:
    return yaml.safe_load((pack_dir / "manifest.yaml").read_text(encoding="utf-8"))


def connect(dbname: str, target: Target) -> psycopg.Connection:
    """Connect as the loader on RDS, or as postgres on the local Docker database."""
    if target == "local":
        return psycopg.connect(host="127.0.0.1", port=5433, user="postgres", dbname=dbname, sslmode="disable")
    settings = get_settings()
    if not settings.pg_loader_user or not settings.pg_loader_password.get_secret_value():
        raise SystemExit("PG_LOADER_USER and PG_LOADER_PASSWORD must be set in .env.local for target rds.")
    return psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=dbname,
        user=settings.pg_loader_user,
        password=settings.pg_loader_password.get_secret_value(),
        sslmode=settings.pg_sslmode,
        connect_timeout=15,
    )


def grant(conn: psycopg.Connection, statement: str, role: str) -> None:
    """Run a GRANT only when the role exists (it does on RDS, not on the local test database)."""
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
        conn.execute(statement.format(role=role))


def drop_smoke(conn: psycopg.Connection) -> None:
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = 'smoke'").fetchone()
    if owner is None:
        return
    if owner[0] == conn.execute("SELECT current_user").fetchone()[0]:
        conn.execute("DROP TABLE smoke")
        print(f"{conn.info.dbname}: dropped smoke table")
    else:
        print(f"{conn.info.dbname}: smoke table is owned by {owner[0]}; drop it as that role")


def copy_frame(conn: psycopg.Connection, table: str, frame) -> None:
    columns = ", ".join(frame.columns)
    rows = frame.astype(object).where(frame.notna(), None)
    with conn.cursor().copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
        for row in rows.itertuples(index=False, name=None):
            copy.write_row(row)
