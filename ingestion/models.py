"""
Structured record schema for parsed statute text.

Why pydantic: this is the same validation library FastAPI uses for request/response
models, so learning it here pays off directly in Step 6. For a grounded legal RAG
system, schema validation at ingestion time is not optional — if a record is missing
a citation or source_url, we want ingestion to fail loudly rather than silently
produce a chunk the generation step could cite without being able to back up.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Subsection(BaseModel):
    """
    One node in a statute's internal hierarchy, e.g. (a), (a)(1), (a)(1)(A).

    California statutes nest up to four levels deep: (a) -> (1) -> (A) -> (i).
    Each node keeps only its own directly-written text in `text`; nested
    provisions live in `subsections`, preserving the legal structure instead
    of flattening it into one paragraph.
    """

    label: str = Field(..., description="This node's own marker, e.g. 'a', '1', 'A', 'i'")
    level: int = Field(..., description="Nesting depth: 0=(a), 1=(1), 2=(A), 3=(i)")
    citation: str = Field(..., description='Full citation to this node, e.g. "Cal. Civ. Code § 1950.5(g)(1)"')
    subsection_path: str = Field(..., description='Just the subsection portion, e.g. "(g)(1)"')
    text: str = Field(..., description="This node's own text only, excluding nested children")
    subsections: list["Subsection"] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Self text plus all nested descendants, resolved into one citation-complete span.

        This is what a citation to this node actually refers to under legal convention —
        citing "(g)" implicitly includes everything nested under it. Kept as a computed
        property (not stored) so the underlying tree has no duplicated text at rest.
        """
        parts = [self.text] if self.text else []
        for child in self.subsections:
            parts.append(f"({child.label}) {child.full_text}")
        return " ".join(p for p in parts if p).strip()


class StatuteSection(BaseModel):
    """One top-level code section, e.g. Cal. Civ. Code § 1950.5, with its full subsection tree."""

    citation: str = Field(..., description='e.g. "Cal. Civ. Code § 1950.5"')
    section_number: str = Field(..., description='e.g. "1950.5"')
    heading: str | None = Field(None, description="Section heading, if the source provides one (CA Civil Code generally does not)")
    jurisdiction: str = Field("CA")
    source_url: str
    intro_text: str = Field("", description="Text appearing before the first lettered subsection, if any")
    subsections: list[Subsection] = Field(default_factory=list)
    legislative_history: str | None = Field(None, description='Raw "(Amended by Stats. ...)" note from the source, if present')

    @property
    def full_text(self) -> str:
        """Whole-section text: intro plus every nested subsection, resolved."""
        parts = [self.intro_text] if self.intro_text else []
        for child in self.subsections:
            parts.append(f"({child.label}) {child.full_text}")
        return " ".join(p for p in parts if p).strip()


class Chunk(BaseModel):
    """
    One retrieval unit: the thing that gets embedded, indexed, and handed to
    the LLM at generation time.

    `text` is NOT just the target node's own text -- it's that node's
    citation-complete text (self + nested descendants) prefixed with the
    directly-written text of every ancestor between it and the section root.
    Without that prefix, a chunk like "(A) Default in the payment of rent."
    would be retrievable with no indication it's one of several enumerated
    grounds under "(b) ... just cause means either of the following: (1)
    At-fault just cause, which means any of the following:" -- context the
    generation step needs to answer accurately and cite correctly.
    """

    chunk_id: str = Field(..., description='Stable, DB/URL-safe id, e.g. "civ_1946.2_b_1_A"')
    citation: str = Field(..., description='e.g. "Cal. Civ. Code § 1946.2(b)(1)(A)"')
    section_number: str
    subsection_path: str | None = Field(None, description="None for a whole-section chunk (no subsections)")
    heading: str | None
    jurisdiction: str
    source_url: str
    chapter_heading: str = Field(..., description='e.g. "CHAPTER 2. Hiring of Real Property [1940 - 1954.071]"')
    text: str = Field(..., description="Ancestor context + this node's own citation-complete text")
    token_count: int
    exceeds_token_budget: bool = Field(
        False, description="True if this chunk is an irreducible leaf larger than the target budget"
    )


class Chapter(BaseModel):
    """Container for the whole scraped chapter, plus the hierarchy context above it."""

    division_heading: str
    part_heading: str
    title_heading: str
    chapter_heading: str
    source_url: str
    sections: list[StatuteSection]
