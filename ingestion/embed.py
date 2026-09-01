"""
Generate embeddings for every chunk in data/processed/chunks.json using
Voyage AI's voyage-law-2 (a legal-domain-tuned embedding model), and write
data/processed/chunks_embedded.json (each chunk plus an "embedding" field).

Why a separate cached-output step instead of embedding inline during DB load:
embedding calls cost money and take network round-trips, while re-running the
DB load (e.g. after a schema tweak) shouldn't require re-hitting the API.
Splitting embed from load means "re-embed" and "re-load" are two independently
re-runnable steps instead of one bundled one.

input_type="document": Voyage's embed models are trained asymmetrically --
documents (what gets indexed) and queries (what a user types at search time)
are embedded slightly differently so that a short question and a longer
statute passage that answers it land close together in vector space, even
though they don't share much surface wording. Every chunk we index here uses
input_type="document"; Step 4's retrieval code must use input_type="query"
for the user's question, or the two embedding spaces won't line up.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import voyageai
from dotenv import load_dotenv

load_dotenv()

MODEL = "voyage-law-2"

# Voyage accounts without a payment method on file get throttled to 3
# requests/minute and 10K tokens/minute (the free-tier token pool -- 200M
# tokens for Voyage series 3 -- still applies underneath that; it's a *rate*
# limit, not a quota problem, for a corpus this size). We batch by token
# count (reusing each chunk's `token_count` from chunk.py as a close-enough
# proxy for Voyage's own tokenizer) and pace requests to stay under both.
MAX_TOKENS_PER_BATCH = 2_000
MAX_ITEMS_PER_BATCH = 25
SECONDS_BETWEEN_REQUESTS = 25  # under 3 requests/minute with margin
MAX_RETRIES = 5

CHUNKS_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "chunks.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "processed" / "chunks_embedded.json"


def _make_batches(chunks: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for chunk in chunks:
        t = chunk["token_count"]
        if current and (current_tokens + t > MAX_TOKENS_PER_BATCH or len(current) >= MAX_ITEMS_PER_BATCH):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(chunk)
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _embed_with_retry(client: voyageai.Client, texts: list[str]):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.embed(texts=texts, model=MODEL, input_type="document")
        except voyageai.error.RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            print(f"  rate limited, waiting {SECONDS_BETWEEN_REQUESTS}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(SECONDS_BETWEEN_REQUESTS)


def embed_chunks(chunks: list[dict], client: voyageai.Client) -> list[dict]:
    batches = _make_batches(chunks)
    embedded = []
    total_tokens = 0
    done = 0
    for i, batch in enumerate(batches):
        resp = _embed_with_retry(client, [c["text"] for c in batch])
        total_tokens += resp.total_tokens
        for chunk, embedding in zip(batch, resp.embeddings):
            embedded.append({**chunk, "embedding": embedding})
        done += len(batch)
        print(f"  embedded {done}/{len(chunks)} (batch {i + 1}/{len(batches)})")
        if i < len(batches) - 1:
            time.sleep(SECONDS_BETWEEN_REQUESTS)
    print(f"Total tokens billed: {total_tokens}")
    return embedded


if __name__ == "__main__":
    if not os.environ.get("VOYAGE_API_KEY"):
        raise SystemExit("VOYAGE_API_KEY is not set. Add it to .env.")

    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    client = voyageai.Client()
    embedded = embed_chunks(chunks, client)

    OUT_PATH.write_text(json.dumps(embedded, indent=2), encoding="utf-8")
    print(f"Wrote {len(embedded)} embedded chunks -> {OUT_PATH}")
