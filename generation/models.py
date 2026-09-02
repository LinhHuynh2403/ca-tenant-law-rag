from __future__ import annotations

from pydantic import BaseModel, Field

from retrieval.models import SearchResult


class AnswerResult(BaseModel):
    """A generated answer plus enough to audit it against what was retrieved."""

    query: str
    answer: str
    sources: list[SearchResult]  # what was offered to the model as context

    cited_citations: list[str]  # citation strings the model's answer actually references
    unverified_citations: list[str]  # cited but NOT among `sources` -- should always be empty

    # Short plain-language label per cited citation, e.g. "Return of security
    # deposit timeline" -- decorative, not part of grounding verification
    # (see generate_answer()'s comment on why citation_labels never feeds
    # is_fully_grounded).
    citation_labels: dict[str, str] = Field(default_factory=dict)

    @property
    def is_fully_grounded(self) -> bool:
        return len(self.unverified_citations) == 0
