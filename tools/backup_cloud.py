"""Portable backup of the live data: a pg_dump (custom format) of the telemetry, cmms and knowledge databases, the FMEA
table as Zetaris serves it from S3, and a manifest with row counts and SHA-256 checksums. Read-only: dumps and SELECTs
only. Credentials come from the app's settings and reach pg_dump through the environment; no host, user or password is
printed or written to the backup. Needs Docker (pg_dump 17 from the same image as the local database).
Run: uv run task backup [--out DIR] [--verify]   (default DIR: ../backups/cleris-<UTC date>, outside the repository)
--verify restores each dump into the local Docker Postgres (uv run task db-up) and compares row counts."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from data.db import connect
from settings import ROOT, get_settings
from tools.zetaris import ZetarisClient, rows_to_frame

IMAGE = "pgvector/pgvector:pg17"        # same image as docker-compose.yml, so pg_dump matches the RDS major version
FMEA_SQL = "SELECT * FROM s3_files.fmea"
NOTE = ("No host, user password or API key is stored. Like every pg_dump archive, the dumps name object owners by role; "
        "restore with --no-owner. Grants are not in the dumps (--no-privileges); RESTORE.txt lists them.")
RESTORE = """Restore into the local Docker Postgres (uv run task db-up in the Cleris repository creates the three databases):
  docker compose exec -T postgres pg_restore -U postgres -d <db> --clean --if-exists --no-owner --no-privileges < <db>.dump
Into a new PostgreSQL 17 (for example RDS) with pgvector available, after CREATE DATABASE <db>:
  pg_restore -h <host> -U <admin user> -d <db> --no-owner --no-privileges <db>.dump

Grants (the roles zetaris_ro and app_rw must exist; the answer key eval_ground_truth stays unreadable to both):
  telemetry:  GRANT SELECT ON cycles, signals, telemetry_long TO zetaris_ro;
  cmms:       GRANT SELECT ON ALL TABLES IN SCHEMA public TO zetaris_ro;
              GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_rw;
  knowledge:  GRANT SELECT ON doc_chunks TO zetaris_ro;

Then upload fmea_from_s3.parquet to s3://<bucket>/fmea/, point the Zetaris sources telemetry_pg, cmms_pg, knowledge_pg
and s3_files at the restored copies, and run uv run task register (it checks that each source returns rows).
Compare row counts and SHA-256 checksums with manifest.json.
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def row_counts(dbname: str) -> dict[str, int]:
    with connect(dbname, "rds") as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_type = 'BASE TABLE' ORDER BY table_name").fetchall()]
        return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}


def server_version(dbname: str) -> str:
    with connect(dbname, "rds") as conn:
        return conn.execute("SHOW server_version").fetchone()[0].split(" ")[0]


def dump(dbname: str, out: Path) -> Path:
    settings = get_settings()
    env = {**os.environ, "PGPASSWORD": settings.pg_loader_password.get_secret_value(), "PGSSLMODE": settings.pg_sslmode}
    target = out / f"{dbname}.dump"
    command = ["docker", "run", "--rm", "-e", "PGPASSWORD", "-e", "PGSSLMODE", "-v", f"{out.resolve()}:/out", IMAGE,
               "pg_dump", "-h", settings.pg_host, "-p", str(settings.pg_port), "-U", settings.pg_loader_user,
               "-d", dbname, "--format=custom", "--no-owner", "--no-privileges", "-f", f"/out/{dbname}.dump"]
    done = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0 or not target.exists():
        # pg_dump's message can name the host; report only that it failed.
        raise SystemExit(f"pg_dump failed for {dbname} (exit {done.returncode}); check the loader settings and Docker")
    return target


def fmea(out: Path) -> tuple[Path, int]:
    frame = rows_to_frame(ZetarisClient(get_settings()).run_select(FMEA_SQL))
    path = out / "fmea_from_s3.parquet"
    frame.to_parquet(path, index=False)
    return path, len(frame)


def verify(out: Path, manifest: dict) -> list[str]:
    """Restore each dump into a scratch database on the local Docker Postgres and compare row counts."""
    problems = []
    for dbname, entry in manifest["databases"].items():
        scratch = f"{dbname}_restore_check"
        psql = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-tAc"]
        subprocess.run([*psql, f"DROP DATABASE IF EXISTS {scratch}"], cwd=ROOT, check=True, capture_output=True)
        subprocess.run([*psql, f"CREATE DATABASE {scratch}"], cwd=ROOT, check=True, capture_output=True)
        with (out / entry["file"]).open("rb") as archive:
            subprocess.run(["docker", "compose", "exec", "-T", "postgres", "pg_restore", "-U", "postgres", "-d", scratch,
                            "--no-owner", "--no-privileges"], cwd=ROOT, stdin=archive, check=True, capture_output=True)
        for table, rows in entry["tables"].items():
            got = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", scratch,
                                  "-tAc", f'SELECT count(*) FROM "{table}"'], cwd=ROOT, check=True, capture_output=True,
                                 text=True).stdout.strip()
            if int(got or -1) != rows:
                problems.append(f"{dbname}.{table}: {got} restored vs {rows} in the manifest")
        subprocess.run([*psql, f"DROP DATABASE IF EXISTS {scratch}"], cwd=ROOT, check=True, capture_output=True)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.pg_loader_user or not settings.pg_loader_password.get_secret_value():
        raise SystemExit("The loader settings are not set; this backup reads the live databases.")
    now = datetime.now(UTC)
    out = (args.out or ROOT.parent / "backups" / f"cleris-{now:%Y-%m-%d}").resolve()
    if out.is_relative_to(ROOT):
        raise SystemExit("Write the backup outside the repository.")
    out.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                            check=False).stdout.strip()
    manifest = {"created_at": now.isoformat(), "repository_commit": commit or "unknown", "pg_dump_image": IMAGE,
                "source": "AWS RDS PostgreSQL (host not recorded)", "databases": {}, "files": {},
                "note": NOTE}
    for dbname in (settings.pg_db_telemetry, settings.pg_db_cmms, settings.pg_db_knowledge):
        tables = row_counts(dbname)
        path = dump(dbname, out)
        manifest["databases"][dbname] = {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path),
                                         "server_version": server_version(dbname), "tables": tables}
        print(f"{dbname}: {len(tables)} tables, {sum(tables.values()):,} rows, {path.stat().st_size / 1e6:.1f} MB")
    path, rows = fmea(out)
    manifest["files"]["fmea"] = {"file": path.name, "rows": rows, "bytes": path.stat().st_size, "sha256": sha256(path),
                                 "source": "Zetaris s3_files.fmea (the S3 file as served)"}
    print(f"fmea: {rows} rows from S3 through Zetaris")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (out / "RESTORE.txt").write_text(RESTORE, encoding="utf-8")
    print(f"backup written to {out}")
    if args.verify:
        problems = verify(out, manifest)
        print("verify: every table restored with the manifest's row count" if not problems else
              "verify FAILED:\n  " + "\n  ".join(problems))
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
