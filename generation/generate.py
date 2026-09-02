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
from pydantic import BaseModel, Field

from generation.models import AnswerResult
from retrieval.models import SearchResult
from retrieval.search import hybrid_search

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """\
You are a legal information assistant for California residential landlord-tenant law.

You will be given excerpts from the California Civil Code, each labeled with its \
exact citation, followed by a user's question. Follow these rules strictly:

1. Answer using ONLY the information in the excerpts provided below. Do not use any \
outside knowledge of California law, even if you believe you know the answer -- the \
excerpts may reflect amendments you are not aware of.
2. After every factual claim, insert the exact citation shown above that excerpt (e.g. \
"Cal. Civ. Code § 1950.5(h)(1)(A)"), copied exactly as written, wrapped in double \
square brackets immediately after the claim, with no other surrounding punctuation --\
e.g. "...within 24 hours[[Cal. Civ. Code § 1954(d)]]." If one claim relies on more \
than one citation, place multiple bracket tags back to back: \
"...[[Cal. Civ. Code § X]][[Cal. Civ. Code § Y]]." Every sentence or bullet that \
states a rule must end with at least one bracketed citation.
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
7. Write in plain text -- no markdown headings, no bullet characters other than a \
plain "-" at the start of its own line. The one exception: you may wrap the single \
most important figure or fact in your opening sentence in "**double asterisks**" for \
emphasis (e.g. "**21 calendar days**") -- do not bold anything else, and do not use \
any other markdown.

FORMAT YOUR ANSWER LIKE THIS:
1. Start with a direct 1-2 sentence answer to the user's actual question. This is the \
most important part -- the user should get the key answer immediately without reading \
further.
2. Only if there are important conditions or exceptions, add a short list (max 3-4 \
lines, each starting with "-", one line each). Omit this if the direct answer is \
already complete.
3. Keep the entire answer under ~120 words unless the user's question asks for detail \
or the excerpts genuinely require more to answer accurately and safely.
4. Do not walk through every excerpt you were given. Cite and mention only what \
directly answers the question -- an excerpt you don't reference won't appear in the \
sources shown to the user, so only cite what you actually relied on.
5. If part of the question isn't covered by your sources, say so in one short sentence \
at the end -- don't elaborate on what's missing.

Lead with the answer. Be concise. The user wants to know quickly, not read a memo.

Also produce `citation_labels`: for each distinct citation you used in the answer, a \
short (3-6 word) plain-language label describing what that specific provision covers \
-- e.g. "Return of security deposit timeline", not a restatement of the rule itself \
and not the citation string again. This is a navigation aid shown above the excerpt \
text, not a source of legal information on its own.
"""

CITATION_RE = re.compile(r"Cal\. Civ\. Code § \d+(?:\.\d+)*(?:\([a-zA-Z0-9]+\))*")


class CitationLabel(BaseModel):
    citation: str
    label: str = Field(..., description="3-6 word plain-language paraphrase, not statute text")


class GeneratedAnswer(BaseModel):
    answer: str
    citation_labels: list[CitationLabel]


def _format_sources(sources: list[SearchResult]) -> str:
    blocks = [f"[{s.citation}]\n{s.text}" for s in sources]
    return "\n\n".join(blocks)


def _extract_citations(text: str) -> list[str]:
    seen: list[str] = []
    for m in CITATION_RE.findall(text):
        if m not in seen:
            seen.append(m)
    return seen


def _citations_related(a: str, b: str) -> bool:
    """True if `a` and `b` refer to the same retrieved text -- exact match,
    or one is an ancestor/descendant of the other (see grounding note below)."""
    return a == b or a.startswith(b) or b.startswith(a)


def find_backing_source(citation: str, sources: list[SearchResult]) -> SearchResult | None:
    """The retrieved chunk that grounds a given cited citation, if any --
    used by the API layer to show the underlying statute text for a citation
    the model produced (which may be an ancestor/descendant of the chunk's
    own citation, not necessarily an exact string match)."""
    return next((s for s in sources if _citations_related(citation, s.citation)), None)


def generate_answer(query: str, top_k: int = 5, client: anthropic.Anthropic | None = None) -> AnswerResult:
    client = client or anthropic.Anthropic()
    sources = hybrid_search(query, top_k=top_k)

    user_message = f"EXCERPTS:\n\n{_format_sources(sources)}\n\nQUESTION: {query}"

    response = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=GeneratedAnswer,
    )
    parsed = response.parsed_output
    answer_text = parsed.answer

    # Grounding verification stays independent of citation_labels -- it's
    # derived only from citation strings found in `answer_text` itself (now
    # inside [[...]] tags rather than parens, same regex either way), never
    # trusting citation_labels as a stand-in for what was actually cited. A
    # decorative label the model produced for a citation it didn't actually
    # use in the answer should never count as "cited".
    cited = _extract_citations(answer_text)
    # A cited section is grounded if it matches a retrieved chunk's citation
    # (find_backing_source), which accepts exact matches AND ancestor/
    # descendant relationships:
    #   - ANCESTOR (cited "(h)(1)", retrieved chunk is "(h)(1)(A)(i)"):
    #     grounded because chunk.py's ancestor-context-prefixing means
    #     "(h)(1)"'s own text is literally inside that chunk's text.
    #   - DESCENDANT (cited "(c)(1)", retrieved chunk is "(c)"): also
    #     grounded, because a chunk only exists for a node when its FULL
    #     subtree fit the token budget as one chunk -- so "(c)"'s chunk text
    #     already contains "(c)(1)"'s text verbatim, nested inside it.
    unverified = [c for c in cited if find_backing_source(c, sources) is None]

    labels = {cl.citation: cl.label for cl in parsed.citation_labels}

    return AnswerResult(
        query=query,
        answer=answer_text,
        sources=sources,
        cited_citations=cited,
        unverified_citations=unverified,
        citation_labels=labels,
    )


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "how many days does a landlord have to return my security deposit?"
    result = generate_answer(query)

    print(f"Query: {result.query}\n")
    print(result.answer)
    print(f"\n--- cited: {result.cited_citations}")
    print(f"--- unverified (should be empty): {result.unverified_citations}")
