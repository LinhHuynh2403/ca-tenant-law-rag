"""
Hybrid retrieval: vector (semantic) search + keyword (full-text) search,
combined with Reciprocal Rank Fusion (RRF), filtered by jurisdiction.

Why hybrid, not vector-only: Step 3's own test query showed the gap --
"how many days does a landlord have to return my security deposit?" ranked
the analogous *non-residential* deposit rule (§1950.7(c)) above the correct
residential one (§1950.5(h)(1)(A)), because embeddings match on meaning, not
exact terms. Keyword search would rank the residential chunk higher since it
literally contains "security deposit". Combining both catches what either one
misses alone.

Why Reciprocal Rank Fusion, not a weighted score blend: vector similarity
(cosine, 0-1) and Postgres's ts_rank_cd (an unbounded float) live on
incompatible scales, so adding them directly would be comparing apples to
oranges, weighted arbitrarily. RRF sidesteps this by using each result's RANK
POSITION in its list instead of its raw score: score = 1/(k + rank), summed
across both lists. A chunk ranking well in both lists wins; a chunk only one
method found still earns partial credit. This is the standard approach for
combining Postgres full-text search with pgvector (see Supabase's hybrid
search guide) and runs as a single SQL query -- no separate scoring pass in
Python.
"""

from __future__ import annotations

import os

import voyageai
from dotenv import load_dotenv
from pgvector import Vector
from psycopg.rows import dict_row

from db.connection import get_connection
from retrieval.models import SearchResult

load_dotenv()

EMBED_MODEL = "voyage-law-2"
RRF_K = 60  # standard default from the RRF literature; dampens the impact of rank 1 vs rank 2
CANDIDATE_LIMIT = 25  # how many results each search pulls before fusion narrows to top_k

_voyage_client: voyageai.Client | None = None


def _get_voyage_client() -> voyageai.Client:
    global _voyage_client
    if _voyage_client is None:
        if not os.environ.get("VOYAGE_API_KEY"):
            raise RuntimeError("VOYAGE_API_KEY is not set. Add it to .env.")
        _voyage_client = voyageai.Client()
    return _voyage_client


def embed_query(query: str) -> list[float]:
    """Embed a user question. input_type="query" -- must match the "document"
    side used in ingestion/embed.py, or the two embedding spaces won't line up."""
    resp = _get_voyage_client().embed(texts=[query], model=EMBED_MODEL, input_type="query")
    return resp.embeddings[0]


HYBRID_SEARCH_SQL = """
WITH query_terms AS (
    -- plainto_tsquery ANDs every significant word together ("landlord" AND
    -- "security" AND "deposit" AND ...), which is far too strict for a full
    -- natural-language question -- almost no single chunk contains every
    -- word. Converting the AND-query's operators to OR turns this into
    -- "match chunks containing ANY of these words", and ts_rank_cd (used
    -- below) naturally scores chunks that match MORE of them higher -- the
    -- same "more overlap = more relevant" behavior classic keyword ranking
    -- (e.g. BM25) gives you, without an all-or-nothing AND gate.
    SELECT to_tsquery('english', regexp_replace(plainto_tsquery('english', %(query_text)s)::text, ' & ', ' | ', 'g')) AS q
),
semantic AS (
    SELECT chunk_id,
           RANK() OVER (ORDER BY embedding <=> %(query_embedding)s) AS rank
    FROM chunks
    WHERE jurisdiction = %(jurisdiction)s
    ORDER BY embedding <=> %(query_embedding)s
    LIMIT %(candidate_limit)s
),
keyword AS (
    SELECT chunks.chunk_id,
           RANK() OVER (ORDER BY ts_rank_cd(text_search, query_terms.q) DESC) AS rank
    FROM chunks, query_terms
    WHERE chunks.jurisdiction = %(jurisdiction)s
      AND text_search @@ query_terms.q
    ORDER BY ts_rank_cd(text_search, query_terms.q) DESC
    LIMIT %(candidate_limit)s
),
fused AS (
    SELECT
        COALESCE(semantic.chunk_id, keyword.chunk_id) AS chunk_id,
        COALESCE(1.0 / (%(rrf_k)s + semantic.rank), 0.0)
            + COALESCE(1.0 / (%(rrf_k)s + keyword.rank), 0.0) AS rrf_score,
        semantic.rank AS semantic_rank,
        keyword.rank AS keyword_rank
    FROM semantic
    FULL OUTER JOIN keyword ON semantic.chunk_id = keyword.chunk_id
)
SELECT
    c.chunk_id, c.citation, c.section_number, c.subsection_path, c.heading,
    c.jurisdiction, c.source_url, c.chapter_heading, c.text,
    f.rrf_score, f.semantic_rank, f.keyword_rank
FROM fused f
JOIN chunks c ON c.chunk_id = f.chunk_id
ORDER BY f.rrf_score DESC
LIMIT %(top_k)s;
"""


def hybrid_search(
    query: str,
    top_k: int = 5,
    jurisdiction: str = "CA",
    candidate_limit: int = CANDIDATE_LIMIT,
    rrf_k: int = RRF_K,
) -> list[SearchResult]:
    query_embedding = Vector(embed_query(query))

    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                HYBRID_SEARCH_SQL,
                {
                    "query_embedding": query_embedding,
                    "query_text": query,
                    "jurisdiction": jurisdiction,
                    "candidate_limit": candidate_limit,
                    "rrf_k": rrf_k,
                    "top_k": top_k,
                },
            )
            rows = cur.fetchall()

    return [SearchResult(**row) for row in rows]


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "how many days does a landlord have to return my security deposit?"
    print(f"Query: {query}\n")
    for r in hybrid_search(query):
        print(f"{r.rrf_score:.4f}  (sem={r.semantic_rank}, kw={r.keyword_rank})  {r.citation}")
        print(f"  {r.text[:150]}...")
