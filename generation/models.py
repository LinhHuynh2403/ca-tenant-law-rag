from __future__ import annotations

from pydantic import BaseModel

from retrieval.models import SearchResult


class AnswerResult(BaseModel):
    """A generated answer plus enough to audit it against what was retrieved."""

    query: str
    answer: str
    sources: list[SearchResult]  # what was offered to the model as context

    cited_citations: list[str]  # citation strings the model's answer actually references
    unverified_citations: list[str]  # cited but NOT among `sources` -- should always be empty

    @property
    def is_fully_grounded(self) -> bool:
        return len(self.unverified_citations) == 0
