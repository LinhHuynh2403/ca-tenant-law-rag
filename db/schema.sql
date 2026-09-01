-- Storage for retrieval chunks: one row per Chunk from ingestion/chunk.py.
--
-- Two search paths live side by side on purpose (Step 4 will combine them):
--   - `embedding`   vector(1024) -- semantic search (pgvector, Voyage AI
--                                  voyage-law-2 dimension)
--   - `text_search` tsvector     -- keyword search (Postgres full-text search)
--
-- `text_search` is a GENERATED column: Postgres derives and stores it
-- automatically from `text` on every insert/update, so it can never drift
-- out of sync with the source text, and we never have to remember to
-- recompute it ourselves.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id              TEXT PRIMARY KEY,
    citation              TEXT NOT NULL,
    section_number        TEXT NOT NULL,
    subsection_path       TEXT,
    heading                TEXT,
    jurisdiction           TEXT NOT NULL,
    source_url             TEXT NOT NULL,
    chapter_heading        TEXT NOT NULL,
    text                   TEXT NOT NULL,
    token_count             INT NOT NULL,
    exceeds_token_budget    BOOLEAN NOT NULL DEFAULT FALSE,
    embedding               VECTOR(1024),
    text_search              TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW: approximate nearest-neighbor index for cosine similarity search.
-- Unlike pgvector's older IVFFlat index, HNSW doesn't need representative
-- data present before it can build a useful index, which matters for a
-- corpus this small (560 rows) that will be rebuilt from scratch often
-- during development.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_text_search_idx
    ON chunks USING gin (text_search);

CREATE INDEX IF NOT EXISTS chunks_jurisdiction_idx
    ON chunks (jurisdiction);

CREATE INDEX IF NOT EXISTS chunks_section_number_idx
    ON chunks (section_number);
