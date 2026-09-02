"""
Grounded, cited answer generation over retrieved statute chunks.

Two layers of grounding, not one:
1. PROMPT-LEVEL: the system prompt instructs Claude to answer only from the
   provided excerpts, cite the exact section after every claim (reusing each
   excerpt's own citation string as its label -- no separate footnote-number
   mapping to keep straight), decline when the excerpts don't cover the
   question, and append a "not legal advice" note.
2. OUTPUT-LEVEL VERIFICATION: prompt instructions are not a guarantee. After
   generation, every "Cal. Civ. Code § ..." citation string in the answer is
   extracted and checked against the citations actually retrieved and shown
   to the model. Any citation that appears in the answer but wasn't in the
   provided sources is flagged in `unverified_citations` -- that's the
   system's own check for whether it's living up to "never fabricate
   citations", not just a hope that the prompt worked.
"""

from __future__ import annotations

import re

import anthropic

from generation.models import AnswerResult
from retrieval.models import SearchResult
from retrieval.search import hybrid_search

MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
You are a legal information assistant for California residential landlord-tenant law.

You will be given excerpts from the California Civil Code, each labeled with its \
exact citation, followed by a user's question. Follow these rules strictly:

1. Answer using ONLY the information in the excerpts provided below. Do not use any \
outside knowledge of California law, even if you believe you know the answer -- the \
excerpts may reflect amendments you are not aware of.
2. After every factual claim, cite the exact source using the citation label shown \
above that excerpt (e.g. "Cal. Civ. Code § 1950.5(h)(1)(A)"), copied exactly as \
written. Every sentence that states a rule must end with its citation.
3. Never cite a section that was not one of the excerpts provided to you. Never \
invent, guess, or paraphrase a section number.
4. If the provided excerpts do not contain enough information to answer the \
question -- including if the excerpts are about a related but different topic -- say \
exactly: "I don't have that in my sources." Do not fill the gap with outside knowledge.
5. If different excerpts appear to conflict or one excerpt limits another's scope \
(e.g. "this section does not apply to X"), reason about which actually governs the \
user's situation and say so explicitly.
6. End every answer, even a refusal, with this exact line on its own: \
"This is general information, not legal advice."
"""

CITATION_RE = re.compile(r"Cal\. Civ\. Code § \d+(?:\.\d+)*(?:\([a-zA-Z0-9]+\))*")


def _format_sources(sources: list[SearchResult]) -> str:
    blocks = [f"[{s.citation}]\n{s.text}" for s in sources]
    return "\n\n".join(blocks)


def _extract_citations(text: str) -> list[str]:
    seen: list[str] = []
    for m in CITATION_RE.findall(text):
        if m not in seen:
            seen.append(m)
    return seen


def generate_answer(query: str, top_k: int = 5, client: anthropic.Anthropic | None = None) -> AnswerResult:
    client = client or anthropic.Anthropic()
    sources = hybrid_search(query, top_k=top_k)

    user_message = f"EXCERPTS:\n\n{_format_sources(sources)}\n\nQUESTION: {query}"

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    answer_text = "".join(b.text for b in response.content if b.type == "text")

    cited = _extract_citations(answer_text)
    known_citations = {s.citation for s in sources}
    # A cited section is grounded if it matches a retrieved chunk's citation
    # in either direction, not just exactly:
    #   - ANCESTOR (cited "(h)(1)", retrieved chunk is "(h)(1)(A)(i)"):
    #     grounded because chunk.py's ancestor-context-prefixing means
    #     "(h)(1)"'s own text is literally inside that chunk's text.
    #   - DESCENDANT (cited "(c)(1)", retrieved chunk is "(c)"): also
    #     grounded, because a chunk only exists for a node when its FULL
    #     subtree fit the token budget as one chunk -- so "(c)"'s chunk text
    #     already contains "(c)(1)"'s text verbatim, nested inside it.
    # Since citation strings are built by concatenating "(label)" segments in
    # order, both relationships are plain string prefixes -- just checked in
    # opposite directions.
    unverified = [
        c for c in cited
        if c not in known_citations and not any(c.startswith(k) or k.startswith(c) for k in known_citations)
    ]

    return AnswerResult(
        query=query,
        answer=answer_text,
        sources=sources,
        cited_citations=cited,
        unverified_citations=unverified,
    )


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "how many days does a landlord have to return my security deposit?"
    result = generate_answer(query)

    print(f"Query: {result.query}\n")
    print(result.answer)
    print(f"\n--- cited: {result.cited_citations}")
    print(f"--- unverified (should be empty): {result.unverified_citations}")
