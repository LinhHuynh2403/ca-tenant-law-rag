"""
Apply db/schema.sql and load data/processed/chunks_embedded.json into Postgres.

Run after ingestion/embed.py has produced chunks_embedded.json. Safe to
re-run: schema creation is idempotent (IF NOT EXISTS everywhere), and loading
upserts by chunk_id (ON CONFLICT DO UPDATE) so re-running after a re-chunk or
re-embed just refreshes existing rows instead of duplicating them.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

from db.connection import DATABASE_URL, get_connection

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
CHUNKS_EMBEDDED_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "chunks_embedded.json"

UPSERT_SQL = """
INSERT INTO chunks (
    chunk_id, citation, section_number, subsection_path, heading,
    jurisdiction, source_url, chapter_heading, text, token_count,
    exceeds_token_budget, embedding
) VALUES (
    %(chunk_id)s, %(citation)s, %(section_number)s, %(subsection_path)s, %(heading)s,
    %(jurisdiction)s, %(source_url)s, %(chapter_heading)s, %(text)s, %(token_count)s,
    %(exceeds_token_budget)s, %(embedding)s
)
ON CONFLICT (chunk_id) DO UPDATE SET
    citation = EXCLUDED.citation,
    section_number = EXCLUDED.section_number,
    subsection_path = EXCLUDED.subsection_path,
    heading = EXCLUDED.heading,
    jurisdiction = EXCLUDED.jurisdiction,
    source_url = EXCLUDED.source_url,
    chapter_heading = EXCLUDED.chapter_heading,
    text = EXCLUDED.text,
    token_count = EXCLUDED.token_count,
    exceeds_token_budget = EXCLUDED.exceeds_token_budget,
    embedding = EXCLUDED.embedding,
    updated_at = now();
"""


def ensure_schema() -> None:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_chunks() -> int:
    rows = json.loads(CHUNKS_EMBEDDED_PATH.read_text(encoding="utf-8"))
    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_SQL, row)
    return len(rows)


if __name__ == "__main__":
    ensure_schema()
    print("Schema applied.")
    n = load_chunks()
    print(f"Loaded/updated {n} chunks into Postgres.")
