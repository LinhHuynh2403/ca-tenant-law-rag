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

> A landlord must return the security deposit within 21 days of the tenant
> vacating [Cal. Civ. Code § 1950.5(g)]. …

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
| Retrieval | **Hybrid: vector + keyword** *(planned)* | Vector catches paraphrase ("deposit return" ≈ "21 days"); keyword catches exact terms and citations that must match precisely. Legal queries need both. |
| Generation | **LLM API** *(planned)* | Composes a cited answer constrained to retrieved text; declines when unsupported. |
| API | **FastAPI** *(planned)* | Async, Pydantic-native, auto-documented HTTP layer. |
| Frontend | **React** *(planned)* | Minimal UI whose job is to make grounding visible: answer + clickable citations. |
| Evaluation | **RAGAS / custom harness** *(planned)* | Measure retrieval hit rate, citation accuracy, and faithfulness — quality as a number, not a vibe. |

---

## Project status

This is an in-progress learning project, built one verified stage at a time.

- [x] **Fetch** — download raw statute HTML
- [x] **Parse** — structured, hierarchy-preserving JSON (92 sections)
- [x] **Chunk** — adaptive, context-prefixed retrieval units (560 chunks)
- [ ] **Embed + index** — pgvector storage
- [ ] **Retrieve** — hybrid vector + keyword search
- [ ] **Generate** — grounded, cited answers
- [ ] **API + UI** — FastAPI endpoint + minimal React frontend
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
├── data/
│   ├── raw/             # raw HTML snapshot
│   └── processed/       # civ_code_ch2_sections.json, chunks.json, chunks_embedded.json
├── docker-compose.yml    # pgvector/pgvector Postgres container
├── pyproject.toml        # dependencies (managed with uv)
├── .env.example          # VOYAGE_API_KEY, DATABASE_URL template
└── README.md
```
*(Layout will grow as later stages are added — retrieval, API, and frontend directories are planned.)*

---

## Getting started

> Setup instructions cover the ingestion pipeline that exists today; embedding,
> retrieval, API, and UI steps will be added as those stages are built.

```bash
# 1. Clone
git clone [YOUR REPO URL]
cd ca-tenant-law-rag

# 2. Install dependencies (uses uv: https://docs.astral.sh/uv/)
uv sync

# 3. Run the ingestion pipeline
uv run python -m ingestion.fetch    # download raw statute HTML
uv run python -m ingestion.parse    # → data/processed/civ_code_ch2_sections.json
uv run python -m ingestion.chunk    # → data/processed/chunks.json

# 4. Start Postgres + pgvector, then embed and load chunks
docker compose up -d
cp .env.example .env   # fill in VOYAGE_API_KEY
uv run python -m ingestion.embed    # → data/processed/chunks_embedded.json
uv run python -m db.load            # applies schema.sql, loads chunks into Postgres
```

---

## Author

**Linh Huynh**

[![Portfolio](https://img.shields.io/badge/Portfolio-000000?style=flat&logo=googlechrome&logoColor=white)](https://linhhuynh2403.github.io/portfolio/)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0A66C2?style=flat&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/linh-huynh-hnvl/)
[![GitHub](https://img.shields.io/badge/GitHub-181717?style=flat&logo=github&logoColor=white)](https://github.com/LinhHuynh2403)

Built as a hands-on exploration of production RAG engineering: data lineage,
structure-aware ingestion, hybrid retrieval, grounding, and evaluation.
