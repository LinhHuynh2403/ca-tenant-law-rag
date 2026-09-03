"""
FastAPI wrapper around the generation pipeline: question in, grounded answer
+ clickable citations out.

Why a plain `def` endpoint, not `async def`: generate_answer() does blocking
I/O throughout (Voyage embedding call, Postgres query, Anthropic call), and
none of the underlying clients are async. FastAPI runs synchronous `def`
path functions in a thread pool automatically, so this stays non-blocking
for other requests without rewriting the whole pipeline as async -- the
right tradeoff for a "minimal UI" step, not something to prematurely
optimize.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.models import AskRequest, AskResponse, CitationInfo
from generation.generate import find_backing_source, generate_answer

app = FastAPI(title="CA Tenant Law RAG API")

# Vite's dev server, always allowed locally, plus whatever the deployed
# frontend's origin is (set ALLOWED_ORIGIN in the backend host's env vars --
# e.g. https://ca-tenant-law-rag.vercel.app, no trailing slash).
_allowed_origins = ["http://localhost:5173"]
if extra_origin := os.environ.get("ALLOWED_ORIGIN"):
    _allowed_origins.append(extra_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/ask")
def ask(req: AskRequest) -> AskResponse:
    result = generate_answer(req.question)

    citations: list[CitationInfo] = []
    for citation in result.cited_citations:
        source = find_backing_source(citation, result.sources)
        if source is None:
            # Should never happen -- would already be in unverified_citations
            # -- but skip rather than show the frontend a citation with no
            # backing text if it somehow does.
            continue
        citations.append(
            CitationInfo(
                citation=citation,
                text=source.text,
                source_url=source.source_url,
                section_number=source.section_number,
                subsection_path=source.subsection_path,
                label=result.citation_labels.get(citation),
            )
        )

    return AskResponse(
        answer=result.answer,
        citations=citations,
        is_fully_grounded=result.is_fully_grounded,
    )
