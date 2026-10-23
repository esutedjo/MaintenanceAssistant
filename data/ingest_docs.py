"""Ingest the asset pack's documents: one chunk per section, hashed-n-gram embedding, written to
data/snapshot/doc_chunks.parquet (sample mode) and, unless --target none, to knowledge.doc_chunks (pgvector).
Run: uv run task ingest-docs [--target rds|local|none]"""

import argparse
import sys

import pandas as pd

from data.db import connect, grant, load_pack
from settings import get_settings
from tools.data_access import DOC_COLUMNS, SNAPSHOT_DIR
from tools.knowledge import DIM, load_chunks

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
DROP TABLE IF EXISTS doc_chunks;
CREATE TABLE doc_chunks (
    chunk_id text PRIMARY KEY,
    doc_id text NOT NULL,
    doc_type text NOT NULL,
    title text NOT NULL,
    revision text NOT NULL,
    effective_date date NOT NULL,
    superseded_by text,
    superseded_on date,
    asset_type text NOT NULL,
    applies_to text NOT NULL,
    section text NOT NULL,
    heading text NOT NULL,
    components text NOT NULL,
    failure_modes text NOT NULL,
    tasks text NOT NULL,
    topic text NOT NULL,
    mandate text NOT NULL,
    text text NOT NULL,
    embedding vector({DIM}) NOT NULL
);
CREATE INDEX doc_chunks_embedding ON doc_chunks USING hnsw (embedding vector_cosine_ops);
COMMENT ON TABLE doc_chunks IS 'SYNTHETIC maintenance documents, one row per section.';
"""


def to_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)[DOC_COLUMNS]


def write_postgres(frame: pd.DataFrame, target: str) -> int:
    with connect(get_settings().pg_db_knowledge, target) as conn:
        conn.execute(DDL)
        insert = (f"INSERT INTO doc_chunks ({', '.join(DOC_COLUMNS)}) VALUES "
                  f"({', '.join(['%s'] * (len(DOC_COLUMNS) - 1))}, %s::vector)")
        rows = []
        for r in frame.to_dict("records"):
            r = {**r, "superseded_by": r["superseded_by"] or None, "superseded_on": r["superseded_on"] or None,
                 "embedding": "[" + ",".join(str(v) for v in r["embedding"]) + "]"}
            rows.append([r[c] for c in DOC_COLUMNS])
        conn.cursor().executemany(insert, rows)
        grant(conn, "GRANT SELECT ON doc_chunks TO {role}", "zetaris_ro")
        conn.commit()
        return conn.execute("SELECT count(*) FROM doc_chunks").fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["rds", "local", "none"], default="rds")
    args = parser.parse_args()
    frame = to_frame(load_chunks(load_pack()))
    path = SNAPSHOT_DIR / "doc_chunks.parquet"
    frame.to_parquet(path, index=False, compression="zstd")
    print(f"snapshot: {len(frame)} chunks from {frame.doc_id.nunique()} documents -> {path}")
    if args.target != "none":
        print(f"knowledge.doc_chunks [{args.target}]: {write_postgres(frame, args.target)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
