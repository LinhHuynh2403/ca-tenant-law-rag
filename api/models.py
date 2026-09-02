from __future__ import annotations

from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str


class CitationInfo(BaseModel):
    """Everything the frontend needs to make a citation clickable and
    verifiable: the citation string itself, the exact statute text it's
    grounded in, and where that text officially comes from."""

    citation: str
    text: str
    source_url: str
    section_number: str
    subsection_path: str | None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationInfo]
    is_fully_grounded: bool
