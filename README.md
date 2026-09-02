# ca-tenant-law-rag

A retrieval-augmented generation (RAG) system that answers questions about
**California residential landlord–tenant law** using only authoritative statute
text, with citations back to the exact code section.

The design goal is **grounded, traceable answers**: every claim the system makes
must trace to a retrieved piece of real statutory text, and the system refuses to
answer when the law it has doesn't cover the question. In legal AI a confident
wrong answer is worse than "I don't know" — so grounding and citation are
treated as core features, not afterthoughts.

> ⚠️ **This project provides general legal information, not legal advice.** It is
> a personal learning project, not a substitute for a licensed attorney.

---

## What it does

Ask a natural-language question about California landlord–tenant law:

> *"In California, how long does a landlord have to return a security deposit?"*

The system retrieves the relevant statute text, and an LLM composes an answer
**constrained to that retrieved text**, citing the exact section:

> No later than 21 calendar days after the tenant has vacated the premises,
> the landlord must furnish an itemized statement and return any remaining
> security (Cal. Civ. Code § 1950.5(h)(1)(A)(i)). …

Each citation is traceable to the underlying statute so the answer can be
verified — the citations are the product.

---

## Why this project

Most portfolio RAG projects are generic "chat with your documents" demos with
naive fixed-size chunking and vector-only search. This one is deliberately:

- **Narrowly scoped** to one real, verifiable legal domain (California Civil Code
  Chapter 2) rather than "all of law."
- **Structure-aware** — statutes are hierarchical, and the pipeline preserves
  that hierarchy instead of flattening it into prose.
- **Grounded by design** — the LLM answers only from retrieved sources and cites
  them; it declines when the corpus doesn't cover a question.
- **Built to be measured** — an evaluation harness (planned) treats answer
  quality as measurable, not vibes.

---

## Architecture

```
California Civil Code (leginfo.legislature.ca.gov)   ← the actual law
        │  fetch
        ▼
   Raw HTML snapshot
        │  parse  (recover hierarchy from CSS indentation)
        ▼
   Structured JSON  (section → subsection tree, citations, metadata)   ← canonical
        │  chunk  (adaptive, structure-first, context-prefixed)
        ▼
   Retrieval chunks  (one per citation-complete unit)                  ← embed these
        │  embed + index
        ▼
   PostgreSQL + pgvector  (vectors + metadata + full text)
        │
  question ─▶ hybrid retrieval (vector + keyword, filtered to CA)
        │  ▶ generation (LLM answers only from retrieved chunks, cited)
        ▼
   Answer + traceable citations  ──▶  FastAPI  ──▶  React UI
```

**The core idea:** the intelligence isn't in the model — it's in the retrieval
and grounding. The LLM is a constrained reasoner over text the system fetches and
verifies, not a source of legal knowledge on its own.

---

## Data source

Legal text is sourced from the **California Civil Code**, published by the
California Legislative Counsel at
[leginfo.legislature.ca.gov](https://leginfo.legislature.ca.gov).

Specifically: Civil Code, Division 3 › Part 4 › Title 5 ›
**Chapter 2 (Hiring of Real Property)**, §§ 1940 – 1954.071 — the core of
California residential landlord–tenant law (including § 1950.5 security deposits
and § 1954 landlord entry).

Direct source URL:
<https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?lawCode=CIV&division=3.&title=5.&part=4.&chapter=2.&article=>

California statutes are in the public domain (government works). Only the
official statutory text is used — not copyrighted third-party summaries or
annotations.

### Currency

The corpus is a **snapshot** of the law as fetched on **2026-09-01**.
Statutes are amended over time (the current snapshot includes amendments
effective January 1, 2026). Answers reflect the law as of the snapshot, not
necessarily current law. To refresh, re-run the fetch → parse → chunk pipeline.

---

## Data pipeline

Raw law → structured data → search-ready chunks. Each stage is a transformation
of the *same* statutory text into a form better suited for the next job.

| Stage | Module | What it produces |
|-------|--------|------------------|
| **Fetch** | `ingestion/fetch.py` | Raw HTML snapshot of the chapter from leginfo. |
| **Parse** | `ingestion/parse.py` | Structured JSON: the section → subsection tree, with citations, jurisdiction, and legislative history. |
| **Chunk** | `ingestion/chunk.py` | Flat list of retrieval units, one per citation-complete subsection. |

Current corpus stats (Chapter 2):

- **92** statute sections parsed
- **560** retrieval chunks
- Chunk token counts: min **12**, median **84**, max **1552**

### Notable design decisions

**Hierarchy is recovered from CSS, not HTML structure.** leginfo renders every
subsection paragraph as a flat sibling `<p>`; nesting is encoded only in each
paragraph's `margin-left` indentation. The parser trusts that visual depth as
ground truth, because the marker text alone is ambiguous — a single character
like `(i)` can be either subsection *(i)* or a roman-numeral item nested several
levels deep, and both readings are locally valid. Trusting the source's own
layout signal avoids silently mis-nesting real subsections.

**Chunking is structure-first and adaptive.** A chunk is one citation-complete
unit (e.g. `§ 1950.5(g)`), not an arbitrary fixed-size slice. But subsections
range from ~12 to ~2400 tokens, so the chunker walks each section top-down: if a
node fits the token budget it becomes one chunk; if it's too big it recurses into
its children instead. This keeps distinct legal rules as distinct vectors (e.g.
each of the ~15 "just cause" eviction grounds becomes its own chunk) rather than
blurring them into one representation.

**Each chunk is context-prefixed to be self-contained.** When a large subsection
is split into its children, the children would otherwise lose their parent's
framing (e.g. `(b) "just cause" means…`). So every chunk's text is prefixed with
the directly-written text of each ancestor on its path, making it interpretable
on its own without needing neighboring chunks.

**Irreducible oversized leaves are emitted whole and flagged.** A few chunks
(e.g. a legal notice template quoted in full inside § 1941.5) exceed the token
budget with no children to split into. These are emitted intact rather than cut
mid-paragraph — a mid-sentence cut could change what the statute asserts — and
flagged with `exceeds_token_budget` for later review.

---

## Retrieval

`retrieval/search.py` runs one SQL query per question that combines two
independent rankings:

- **Semantic** — cosine distance between the query's embedding (Voyage
  `voyage-law-2`, `input_type="query"`) and each chunk's stored embedding.
- **Keyword** — Postgres full-text search (`ts_rank_cd` over the `text_search`
  column), matching on stemmed word overlap rather than meaning.

The two are combined with **Reciprocal Rank Fusion**: each result's score is
`1/(60 + its rank)` in a given list, summed across both lists. This avoids
comparing incompatible scales (cosine similarity is 0–1; `ts_rank_cd` is an
unbounded float) — RRF only cares about rank position, not raw score
magnitude. A `jurisdiction = 'CA'` filter runs in both branches of the query.

### Notable design decisions

**Keyword matching uses OR, not the default AND.** Postgres's
`websearch_to_tsquery` — the obvious first choice — ANDs every significant
word in the query together. For a full sentence question ("how many days does
a landlord have to return my security deposit?"), that means a chunk would
need to contain *all six* significant words to match at all, which almost
never happens in a ~100-token chunk. The fix: build the query with
`plainto_tsquery` and rewrite its `&` operators to `|` (a documented Postgres
idiom), so a chunk matches if it contains *any* of the significant words, and
`ts_rank_cd` naturally scores chunks with more overlap higher — the same
"more overlap, more relevant" behavior classic keyword ranking (e.g. BM25)
gives you, without an all-or-nothing gate.

**A known, deliberately deferred limitation: statutory cross-references.**
Testing turned up a real case: asking about security deposit return timing
ranks `§1950.7(c)` (deposit rules for *non-residential* property) above the
correct `§1950.5(h)` — both by embedding similarity and keyword overlap, since
the two sections describe near-identical mechanics in similar language. The
sentence that would disambiguate this — `§1950.7(a)`: *"With respect to
residential property, the provisions of Section 1950.5 shall prevail"* —
lives in a **sibling** subsection, not an ancestor, so chunking's
context-prefixing (which only walks up the tree) never attaches it to
`§1950.7(c)`'s chunk. Fixing this generally (detecting which subsections carry
section-wide scope language) is exactly the kind of failure a labeled
evaluation harness should catch systematically rather than something to patch
from one anecdote — left for the **Evaluate** stage rather than fixed here.

**Retrieval doesn't decide when to refuse to answer.** An out-of-scope test
query ("penalty for jaywalking") still returned top-5 results with RRF scores
in the same numeric range as genuinely relevant queries — RRF scores reflect
rank position, not whether a result is actually a good match, so there's no
safe score threshold to filter on here. Refusing ("I don't have that in my
sources") has to be a judgment the generation step makes by reading the
retrieved text, not something retrieval can decide on its own — confirming the
original plan's design (grounded refusal lives in Step 5, not Step 4).

---

## Generation

`generation/generate.py` sends the top-K retrieved chunks plus the user's
question to **Claude Sonnet 5** in a single call. Grounding is enforced at two
independent layers, not one:

1. **Prompt-level.** The system prompt instructs Claude to answer only from
   the provided excerpts, cite the exact section after every claim (reusing
   each excerpt's own citation string as its label), reason explicitly about
   conflicting or scope-limited excerpts, decline with an exact phrase
   ("I don't have that in my sources.") when the excerpts don't cover the
   question, and close every answer with a "not legal advice" line.
2. **Output-level verification.** Prompt instructions are not a guarantee.
   After generation, every citation string in the answer is extracted and
   checked against what was actually retrieved — this is the system checking
   its own claim to never fabricate citations, not just trusting the prompt
   worked. Any citation that can't be traced to a retrieved chunk is
   surfaced via `unverified_citations`, which should always be empty.

### Notable design decisions

**Grounding verification accepts ancestor and descendant citations, not just
exact matches.** The first real test run flagged `§1950.5(h)(1)` as
"unverified," even though only `§1950.5(h)(1)(A)(i)` was retrieved. That
wasn't a bug in the answer — the model cited the correct parent subsection as
shorthand, and thanks to chunking's ancestor-context-prefixing (see the
Chunk section above), `(h)(1)`'s own text is genuinely present verbatim
inside the `(h)(1)(A)(i)` chunk shown to the model. The mirror case
(descendant citations, e.g. citing `§1950.7(c)(1)` when only the whole
`§1950.7(c)` chunk was retrieved) is grounded for the same reason in
reverse: a chunk only exists for a node when its *entire* subtree fit the
token budget as one chunk, so its text already contains every descendant's
text nested inside it. Since citation strings are built by concatenating
`(label)` segments in order, both relationships reduce to a plain string
prefix check, just in opposite directions.

**A real test of the §1950.5/§1950.7 distractor from Step 4 showed the
system handling it well, unprompted.** Asked about deposit return timing,
Claude cited the correct `§1950.5(h)(1)` rule, and when it noticed
`§1950.7(c)` also describes deposit deadlines, it explicitly said it could
not determine from the given excerpts whether `§1950.7` applies to
residential tenancies — rather than either wrongly treating it as equally
applicable, or confidently (and ungroundedly) asserting it doesn't apply
using outside knowledge. That is the grounding rule working as intended: the
model reasoned about scope conflict using only what was retrieved, and
declined to go further than the sources allowed.

**Answers are prompted to be concise: a direct 1-2 sentence answer first,
then a short "-"-prefixed list only for genuinely important exceptions,
under ~120 words unless the question needs more.** Early answers ran
300-450 words, walking through every retrieved excerpt in full prose —
technically grounded, but a user asking "how many days" had to read several
paragraphs to find the number. Tightening the prompt cut typical answers to
~110-150 words without losing grounding: fewer excerpts get cited per
answer, but every citation kept is still verified exactly as before (a
citation that's cut for brevity just doesn't reach `cited_citations`, so it
never appears in the API's citation list either — the Sources shown to the
user always match what the answer actually relies on). One prompt-tuning
detail worth remembering: rule 7 originally banned all bullet characters to
stop literal `**`/`#` from leaking into the plain-text UI (see below), but
that also suppressed the short exception-lists this format wants — a plain
`-` at line start needed to be explicitly carved out as safe, since it
reads fine as a bullet without any markdown rendering at all.

**Citations moved from inline parenthetical prose to a `[[citation]]` tag
format, and generation switched to structured output
(`client.messages.parse()`).** The UI redesign needed two things the old
format couldn't give it: a numbered footnote marker in place of each
citation (so `(Cal. Civ. Code § 1954(d))` becomes a small clickable `¹`
linking to its source card), and a short plain-language label per citation
for the source cards (e.g. "21-day deposit return deadline") — data we
don't have, since our chunks carry no human-authored headings. Reliably
stripping natural parenthetical prose back out client-side (especially
where one clause cites multiple sections at once) turned out to be fragile
regex territory; switching the model's citation format to an unambiguous
`[[...]]` tag made that a trivial split, and asking for `citation_labels` as
a structured field alongside the answer (via a `GeneratedAnswer` Pydantic
schema) got a genuine label without a second model call. Grounding
verification deliberately still runs only against citations found in
`answer_text` itself — `citation_labels` is decorative and never treated as
a stand-in for what was actually cited, so a mislabeled or extra entry
there can't silently weaken `is_fully_grounded`.

---

## API + UI

**`api/main.py`** is a thin FastAPI wrapper: one `POST /api/ask` endpoint
that calls `generate_answer()` and reshapes the result for the frontend —
each *cited* citation (not every retrieved chunk) paired with the exact
statute text that grounds it via `find_backing_source()`, plus
`is_fully_grounded` so the UI can surface a warning if verification ever
fails. The endpoint is a plain `def`, not `async def`: `generate_answer()`
is blocking end-to-end (Voyage, Postgres, Anthropic are all synchronous
calls), and FastAPI runs sync path functions in a thread pool automatically
— rewriting the whole pipeline as async wasn't worth it for a "minimal UI"
step.

**`frontend/`** is a minimal Vite + React + TypeScript page: a question box,
the answer, and a "Sources" list of citation cards — each a real link
(`<a target="_blank">`, not a JS toggle) showing the section number and a
text preview, opening straight to the official statute. No component
library, no state management library — one `fetch` call and `useState`,
matching what a "minimal UI whose job is to make grounding visible"
actually needs.

**Visual design pass.** Light-only palette (soft off-white background, no
dark mode — a legal-reference tool reads better calm and paper-like than in
a dark theme), one muted slate-blue accent used sparingly, a serif face
(Source Serif 4) for the long-form answer text specifically since
extended legal reading benefits from it, system-ui everywhere else. Three
response states are handled distinctly rather than collapsed into one
"result" view: a normal grounded answer, a calm muted panel for the
zero-citations refusal case (relying on the fact that a refusal always
carries zero citations, enforced by the Step 5 system prompt — not string-
matching the refusal text), and a red-toned banner reserved for actual
request failures. Verified across desktop and a 390px mobile viewport with
headless-Chromium screenshots, the same way Step 6's first UI pass was.

**Second design pass: landing-page layout with footnote-style citations.**
Full-width nav (logo, "How it works" / "Coverage" / "About" — each opening a
modal with real content rather than living in the page flow, GitHub link)
over a hero search bar, topic chips that auto-submit real example
questions, and citations rendered as numbered
footnotes (`¹`) linking down to numbered source cards — built via
`richText.tsx`, a small custom parser for the `[[citation]]`/`**bold**`
markup the backend now emits (see the Generation section above), not a
markdown library. Two things were deliberately *not* copied from the
reference design rather than faked: a courts.gov "self-help center" link
with no verified URL stayed plain text instead of guessing one, and the
`Discrimination` example topic was swapped for `Just cause eviction` after
checking the corpus directly — Chapter 2 has zero mentions of
discrimination, so shipping that chip would have demoed a feature that
always refuses. Browser verification this time caught two real bugs a
visual read of the code wouldn't have: the nav wrapped mid-word on a 390px
viewport (fixed with `white-space: nowrap` + a stacked mobile layout), and
a `fade-in` CSS animation made answer text render almost invisible in
full-page mobile screenshots — traced to Playwright's screenshot stitching
interacting with an in-progress animation, not a real rendering bug, but
serious enough (and inessential enough, given "no heavy animations" was
already a constraint) to just remove rather than chase further.

**Third pass: info content moved into a modal (`InfoModal.tsx`).** The
"How it works" / "Coverage" / "About" sections originally lived inline,
always visible below the answer — on the request to keep the home page
"as simple as possible," they moved into a shared modal component instead,
triggered by the nav buttons and closable via the × button, clicking the
backdrop, or Escape. With no result on screen, the home page is now exactly
one viewport tall on desktop: hero, search, chips, disclaimer, nothing to
scroll past.

### Running it locally

```bash
# Terminal 1 -- API (from the repo root)
uv run uvicorn api.main:app --reload --port 8000

# Terminal 2 -- frontend
cd frontend
pnpm install
pnpm dev   # http://localhost:5173
```

### Notable design decisions

**Citations shown to the user are the *cited* set, not the *retrieved*
set.** `hybrid_search` returns up to `top_k` chunks (5 by default) as
candidate context, but the model may only end up citing some of them — the
API only returns `CitationInfo` for citations that actually appear in the
answer text, resolved back to their backing chunk via the same
ancestor/descendant matching used for grounding verification (see the
Generation section above). Showing every retrieved-but-unused chunk would
misrepresent what the answer actually relies on.

**Verified in a real browser, not just via `curl`.** Automated
Playwright-driven testing (headless Chromium, screenshots, console-error
checks) confirmed the full loop end-to-end: submitting a question renders
the answer, citation cards expand to show real statute text, and the
"View official source" link works — not just that the API returns valid
JSON. This caught a real, purely cosmetic bug curl testing couldn't have:
the model's answers used markdown (`**bold**`), which isn't rendered by
this plain-text UI and showed up as literal asterisks. Fixed with a
one-line addition to the system prompt rather than adding a markdown
parser dependency for a "minimal" UI.

---

## Tech stack

| Layer | Tool | Why |
|-------|------|-----|
| Language | **Python** | The AI/data-engineering ecosystem lives here. |
| Parsing | **BeautifulSoup** | Extract structured text from leginfo's raw HTML. |
| Data modeling | **Pydantic** | Enforce the record/citation schema at the boundary so malformed data can't flow downstream. |
| Tokenization | **tiktoken** | Estimate chunk token sizes for the adaptive budget. |
| Dependency management | **uv** | Fast, reproducible Python env + lockfile — modern replacement for pip/venv juggling. |
| Embeddings | **Voyage AI (`voyage-law-2`)** | Legal-domain-tuned embedding model; asymmetric `input_type` (document vs. query) improves match quality between short questions and longer statute passages. |
| Storage & search | **PostgreSQL + pgvector** (via Docker) | Keep vectors **and** metadata in one store, so filtering (jurisdiction, date) and semantic search happen in a single query — no second database to sync. |
| Retrieval | **Hybrid: vector + keyword, fused with RRF** | Vector catches paraphrase ("deposit return" ≈ "21 days"); keyword catches exact terms and citations that must match precisely. Legal queries need both. |
| Generation | **Claude Sonnet 5 (Anthropic)** | Composes a cited answer constrained to retrieved text; declines when unsupported. ~$0.008/question — chosen over Opus 5 to keep ongoing per-question cost lower while testing. Paired with Voyage AI (Anthropic's recommended embedding partner, since Claude has no embeddings endpoint of its own) to keep the stack coherent. |
| API | **FastAPI** | Pydantic-native HTTP layer; reuses the same `AskRequest`/`AskResponse` models the rest of the pipeline already speaks. |
| Frontend | **React + TypeScript (Vite)** | Minimal UI whose job is to make grounding visible: answer + clickable citations that expand to the exact retrieved statute text. |
| Evaluation | **RAGAS / custom harness** *(planned)* | Measure retrieval hit rate, citation accuracy, and faithfulness — quality as a number, not a vibe. |

---

## Project status

This is an in-progress learning project, built one verified stage at a time.

- [x] **Fetch** — download raw statute HTML
- [x] **Parse** — structured, hierarchy-preserving JSON (92 sections)
- [x] **Chunk** — adaptive, context-prefixed retrieval units (560 chunks)
- [x] **Embed + index** — 560 chunks embedded (Voyage `voyage-law-2`) and loaded into PostgreSQL + pgvector
- [x] **Retrieve** — hybrid vector + keyword search, fused with RRF, filtered by jurisdiction
- [x] **Generate** — grounded, cited answers (Claude Sonnet 5) with output-level citation verification
- [x] **API + UI** — FastAPI endpoint + minimal React frontend, verified end-to-end in a real browser
- [ ] **Evaluate** — retrieval/citation/faithfulness harness

---

## Repository layout

```
ca-tenant-law-rag/
├── ingestion/
│   ├── fetch.py         # download raw statute HTML from leginfo
│   ├── parse.py         # HTML → structured section/subsection JSON
│   ├── chunk.py         # structured JSON → retrieval chunks
│   ├── embed.py         # chunks → chunks + Voyage AI embeddings
│   └── models.py        # Pydantic data models (Chapter, StatuteSection, Subsection, Chunk)
├── db/
│   ├── schema.sql       # chunks table: vector + tsvector + metadata columns
│   ├── connection.py    # shared Postgres connection helper
│   └── load.py          # applies schema.sql, upserts embedded chunks into Postgres
├── retrieval/
│   ├── search.py        # hybrid_search(): vector + keyword, fused with RRF
│   └── models.py        # SearchResult
├── generation/
│   ├── generate.py      # generate_answer(): prompts Claude, verifies citations
│   └── models.py        # AnswerResult
├── api/
│   ├── main.py          # FastAPI app: POST /api/ask
│   └── models.py        # AskRequest, AskResponse, CitationInfo
├── frontend/             # Vite + React + TypeScript UI
│   └── src/
│       ├── App.tsx       # nav, hero/search, answer, sources, info sections
│       ├── CitationCard.tsx  # numbered source card -> excerpt + link
│       ├── InfoModal.tsx # "How it works" / "Coverage" / "About" popup
│       ├── richText.tsx  # renders [[citation]] -> footnote, **bold**
│       └── api.ts        # fetch client for POST /api/ask
├── data/
│   ├── raw/             # raw HTML snapshot
│   └── processed/       # civ_code_ch2_sections.json, chunks.json, chunks_embedded.json
├── docker-compose.yml    # pgvector/pgvector Postgres container
├── pyproject.toml        # dependencies (managed with uv)
├── .env.example          # VOYAGE_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL template
└── README.md
```
*(Layout will grow next with the evaluation harness.)*

---

## Getting started

> The evaluation harness is the only stage left to build; everything below
> runs end-to-end, from raw statute HTML to a working Q&A page.

```bash
# 1. Clone
git clone https://github.com/LinhHuynh2403/ca-tenant-law-rag.git
cd ca-tenant-law-rag

# 2. Install dependencies (uses uv: https://docs.astral.sh/uv/)
uv sync

# 3. Run the ingestion pipeline
uv run python -m ingestion.fetch    # download raw statute HTML
uv run python -m ingestion.parse    # → data/processed/civ_code_ch2_sections.json
uv run python -m ingestion.chunk    # → data/processed/chunks.json

# 4. Start Postgres + pgvector, then embed and load chunks
docker compose up -d
cp .env.example .env   # fill in VOYAGE_API_KEY, ANTHROPIC_API_KEY
uv run python -m ingestion.embed    # → data/processed/chunks_embedded.json
uv run python -m db.load            # applies schema.sql, loads chunks into Postgres

# 5. Run the API (terminal 1)
uv run uvicorn api.main:app --reload --port 8000

# 6. Run the frontend (terminal 2)
cd frontend && pnpm install && pnpm dev   # → http://localhost:5173
```

---

## Author

**Linh Huynh**

[![Portfolio](https://img.shields.io/badge/Portfolio-000000?style=flat&logo=googlechrome&logoColor=white)](https://linhhuynh2403.github.io/portfolio/)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0A66C2?style=flat&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/linh-huynh-hnvl/)
[![GitHub](https://img.shields.io/badge/GitHub-181717?style=flat&logo=github&logoColor=white)](https://github.com/LinhHuynh2403)

Built as a hands-on exploration of production RAG engineering: data lineage,
structure-aware ingestion, hybrid retrieval, grounding, and evaluation.
