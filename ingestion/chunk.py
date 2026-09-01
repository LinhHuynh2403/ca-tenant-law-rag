"""
Turn the parsed statute hierarchy (Chapter -> StatuteSection -> Subsection
tree) into a flat list of Chunk records for embedding and retrieval.

Chunking strategy: structure-first, adaptive, context-prefixed
----------------------------------------------------------------
A "chunk" should be exactly one citation-complete unit -- the natural
citation grain the user asked for ("Cal. Civ. Code § 1950.5(g)"), not an
arbitrary fixed-size slice of text. But citation units vary wildly in size:
median subsection is ~80 tokens, while e.g. § 1946.2(b) (the "just cause"
eviction list) is ~2400 tokens with 15 enumerated grounds nested inside it.
Embedding that whole thing as one vector would blur 15 distinct legal rules
into a single representation and hurt precision for questions about any one
of them.

So: walk each section's tree top-down. At each node, check whether its
resolved text (itself + everything nested under it) fits inside a token
budget. If it fits, that node becomes one chunk. If not, don't chunk it
directly -- recurse into its children instead, each becoming its own chunk
(or splitting further, same rule, recursively).

The one thing this scheme can silently lose is context: if § 1946.2(b) is
too big and gets split into (b)(1)(A), (b)(1)(B), etc., then (b)'s own
framing sentence ("'just cause' means either of the following:") and (b)(1)'s
("At-fault just cause, which means any of the following:") would otherwise
never appear in any chunk. So every emitted chunk's `text` is prefixed with
the directly-written text of each ancestor on the path from the section root,
in order -- making each chunk interpretable on its own, without needing
neighboring chunks for context.

A chunk only exceeds the budget when it's an irreducible leaf (no children
left to split into) that is itself larger than the budget on its own -- e.g.
a legal notice template quoted in full inside § 1941.5. We emit it whole
rather than cutting into it, since a mid-paragraph cut could change what the
retrieved text actually asserts.
"""

from __future__ import annotations

import re
from pathlib import Path

import tiktoken

from ingestion.models import Chapter, Chunk, StatuteSection, Subsection

DEFAULT_MAX_TOKENS = 500

# cl100k_base is a stand-in token-count estimate, not the exact tokenizer of
# whichever embedding/generation model we end up calling in later steps --
# there's no public tokenizer package for Claude models. It's close enough
# to size chunks sensibly; being off by a small percentage doesn't matter
# here since the budget itself is a soft heuristic, not a hard model limit.
_ENCODING = tiktoken.get_encoding("cl100k_base")


def _token_count(text: str) -> int:
    return len(_ENCODING.encode(text))


def _slug(section_number: str, path_labels: list[str]) -> str:
    base = f"civ_{section_number}"
    if path_labels:
        base += "_" + "_".join(path_labels)
    return base


def _chunks_for_section(
    section: StatuteSection, chapter_heading: str, max_tokens: int, warnings: list[str]
) -> list[Chunk]:
    chunks: list[Chunk] = []

    def make_chunk(node: Subsection, ancestor_context: str, path_labels: list[str]) -> Chunk:
        own = f"({node.label}) {node.full_text}"
        text = f"{ancestor_context} {own}".strip() if ancestor_context else own
        tok = _token_count(text)
        if tok > max_tokens:
            warnings.append(
                f"{node.citation}: {tok} tokens exceeds budget {max_tokens} "
                f"(irreducible leaf -- emitted whole)."
            )
        return Chunk(
            chunk_id=_slug(section.section_number, path_labels),
            citation=node.citation,
            section_number=section.section_number,
            subsection_path=node.subsection_path,
            heading=section.heading,
            jurisdiction=section.jurisdiction,
            source_url=section.source_url,
            chapter_heading=chapter_heading,
            text=text,
            token_count=tok,
            exceeds_token_budget=tok > max_tokens,
        )

    def recurse(node: Subsection, ancestor_context: str, path_labels: list[str]) -> None:
        own = f"({node.label}) {node.full_text}"
        text = f"{ancestor_context} {own}".strip() if ancestor_context else own
        fits = _token_count(text) <= max_tokens

        if fits or not node.subsections:
            chunks.append(make_chunk(node, ancestor_context, path_labels))
            return

        # Too big, but splittable: recurse into children, carrying this
        # node's own (non-nested) text forward as added ancestor context.
        own_line = f"({node.label}) {node.text}" if node.text else f"({node.label})"
        new_context = f"{ancestor_context} {own_line}".strip() if ancestor_context else own_line
        for child in node.subsections:
            recurse(child, new_context, path_labels + [child.label])

    if not section.subsections:
        text = section.full_text or section.intro_text
        tok = _token_count(text)
        chunks.append(
            Chunk(
                chunk_id=_slug(section.section_number, []),
                citation=section.citation,
                section_number=section.section_number,
                subsection_path=None,
                heading=section.heading,
                jurisdiction=section.jurisdiction,
                source_url=section.source_url,
                chapter_heading=chapter_heading,
                text=text,
                token_count=tok,
                exceeds_token_budget=tok > max_tokens,
            )
        )
        if tok > max_tokens:
            warnings.append(f"{section.citation}: {tok} tokens exceeds budget {max_tokens} (whole section, no subsections to split).")
        return chunks

    base_context = section.intro_text or ""
    for top in section.subsections:
        recurse(top, base_context, [top.label])

    return chunks


def build_chunks(chapter: Chapter, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[Chunk]:
    warnings: list[str] = []
    all_chunks: list[Chunk] = []
    for section in chapter.sections:
        all_chunks.extend(_chunks_for_section(section, chapter.chapter_heading, max_tokens, warnings))

    for w in warnings:
        print(f"WARNING: {w}")

    ids = [c.chunk_id for c in all_chunks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate chunk_id(s) generated: {dupes}")

    return all_chunks


if __name__ == "__main__":
    from ingestion.fetch import RAW_HTML_PATH
    from ingestion.parse import parse_chapter

    chapter = parse_chapter(RAW_HTML_PATH.read_text(encoding="utf-8"))
    chunks = build_chunks(chapter)

    out_path = Path(__file__).resolve().parent.parent / "data" / "processed" / "chunks.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "[\n" + ",\n".join(c.model_dump_json(indent=2) for c in chunks) + "\n]",
        encoding="utf-8",
    )

    tokens = [c.token_count for c in chunks]
    print(f"\nBuilt {len(chunks)} chunks -> {out_path}")
    print(f"token_count: min={min(tokens)} median={sorted(tokens)[len(tokens)//2]} max={max(tokens)}")
    print(f"chunks exceeding budget: {sum(c.exceeds_token_budget for c in chunks)}")
