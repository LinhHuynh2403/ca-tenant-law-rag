from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    """One retrieved chunk, with enough metadata to cite and enough score
    detail to debug why it was retrieved."""

    chunk_id: str
    citation: str
    section_number: str
    subsection_path: str | None
    heading: str | None
    jurisdiction: str
    source_url: str
    chapter_heading: str
    text: str

    rrf_score: float
    semantic_rank: int | None = None  # None = not found by vector search
    keyword_rank: int | None = None  # None = not found by keyword search
