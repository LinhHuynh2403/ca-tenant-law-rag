import { useState, type FormEvent } from "react";
import { askQuestion, type AskResponse } from "./api";
import { CitationCard } from "./CitationCard";
import { InfoModal, type InfoContent } from "./InfoModal";
import { renderRichText } from "./richText";
import "./App.css";

const DISCLAIMER_LINE = "This is general information, not legal advice.";

const INFO_CONTENT: Record<string, InfoContent> = {
  "how-it-works": {
    title: "How it works",
    body: "This tool retrieves the exact statute text most relevant to your question from a database of California Civil Code sections — combining semantic search with keyword search — then asks an AI model to compose an answer using only that retrieved text, never its own general knowledge. Every citation is checked against what was actually retrieved before it's shown to you.",
  },
  coverage: {
    title: "Coverage",
    body: "Currently indexed: California Civil Code, Division 3 › Part 4 › Title 5 › Chapter 2 (Hiring of Real Property), §§1940–1954.071 — the core of California residential landlord-tenant law, including security deposits, habitability, notice requirements, just cause eviction, and landlord entry. Other areas of California law, including fair housing and discrimination law, are not yet indexed and questions about them will come back unanswered rather than guessed at.",
  },
  about: {
    title: "About",
    body: "Built as a hands-on exploration of production RAG (retrieval-augmented generation) engineering — structure-aware ingestion, hybrid retrieval, and citation-verified generation. This is a personal learning project, not a commercial product or a substitute for legal counsel.",
  },
};

// The backend appends this line to every answer (see generation/generate.py's
// system prompt). The footer shows a permanent disclaimer already, so strip
// the inline copy rather than show it twice.
function stripDisclaimer(answer: string): string {
  return answer
    .split("\n")
    .filter((line) => line.trim() !== DISCLAIMER_LINE)
    .join("\n");
}

// Real example questions, each mapped to a topic genuinely covered by the
// indexed chapter (CA Civil Code §§1940-1954.071). "Discrimination" was
// deliberately left out here -- verified against the actual corpus, that
// chapter doesn't cover fair housing law, so it would always refuse and be
// a misleading example to show.
const TOPICS: { label: string; query: string }[] = [
  { label: "Security deposit", query: "How many days does a landlord have to return my security deposit?" },
  { label: "Habitability", query: "What conditions must a landlord maintain for a unit to be habitable?" },
  { label: "Entry rights", query: "How much notice does my landlord need to give before entering my apartment?" },
  { label: "Notice to vacate", query: "How much notice does my landlord have to give to end my tenancy?" },
  { label: "Rent increases", query: "How much can my landlord raise my rent, and how often?" },
  { label: "Just cause eviction", query: "Can my landlord evict me without giving a reason?" },
  { label: "Repairs & maintenance", query: "What repairs is my landlord legally required to make?" },
];

function SearchIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" aria-hidden="true">
      <circle cx="9" cy="9" r="6.5" stroke="currentColor" strokeWidth="1.6" />
      <line x1="14" y1="14" x2="18" y2="18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function App() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeInfo, setActiveInfo] = useState<string | null>(null);

  async function runQuery(q: string) {
    const trimmed = q.trim();
    if (!trimmed || loading) return;

    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await askQuestion(trimmed));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    runQuery(question);
  }

  function handleTopicClick(topic: { label: string; query: string }) {
    setQuestion(topic.query);
    runQuery(topic.query);
  }

  // A refusal always carries zero citations (enforced by the generation
  // prompt) -- that's the reliable signal for "no answer found," distinct
  // from a network/API failure.
  const noAnswerFound = result !== null && result.citations.length === 0;

  return (
    <div className="page" id="top">
      <div className="top-strip" />

      <nav className="site-nav">
        <div className="container nav-inner">
          <a className="brand" href="#top">
            <span className="brand-icon" aria-hidden="true">
              ⚖
            </span>
            CA Tenant Law Assistant
          </a>
          <div className="nav-links">
            <button type="button" onClick={() => setActiveInfo("how-it-works")}>
              How it works
            </button>
            <button type="button" onClick={() => setActiveInfo("coverage")}>
              Coverage
            </button>
            <button type="button" onClick={() => setActiveInfo("about")}>
              About
            </button>
            <a href="https://github.com/LinhHuynh2403/ca-tenant-law-rag" target="_blank" rel="noreferrer">
              GitHub
            </a>
          </div>
        </div>
      </nav>

      <main className="container">
        <section className="hero">
          <h1>
            California tenant law,
            <br />
            <em>answered with sources</em>
          </h1>
          <p className="subtitle">
            Ask any landlord-tenant question. Every answer cites the exact California statute
            behind it — so you can read the law yourself.
          </p>

          <form onSubmit={handleSubmit} className="ask-form">
            <span className="search-icon">
              <SearchIcon />
            </span>
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. How many days does a landlord have to return my security deposit?"
              disabled={loading}
            />
            <button type="submit" disabled={loading || !question.trim()}>
              {loading ? "Asking…" : "Ask"}
            </button>
          </form>

          <div className="topic-chips">
            {TOPICS.map((topic) => (
              <button
                key={topic.label}
                type="button"
                className={`chip${question === topic.query ? " chip-active" : ""}`}
                onClick={() => handleTopicClick(topic)}
                disabled={loading}
              >
                {topic.label}
              </button>
            ))}
          </div>
        </section>

        {error && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}

        {result && !noAnswerFound && (
          <div className="result-card">
            {!result.is_fully_grounded && (
              <div className="banner banner-warning">
                One or more citations in this answer could not be verified against the retrieved
                sources.
              </div>
            )}

            <h2 className="eyebrow">Answer</h2>
            <div className="answer-text">
              {stripDisclaimer(result.answer)
                .split("\n")
                .filter((line) => line.trim().length > 0)
                .map((line, i) => (
                  <p key={i}>{renderRichText(line, result.citations)}</p>
                ))}
            </div>

            <div className="sources">
              <h2 className="eyebrow">Sources</h2>
              <div className="citation-list">
                {result.citations.map((c, i) => (
                  <CitationCard key={c.citation} citation={c} index={i + 1} />
                ))}
              </div>
            </div>
          </div>
        )}

        {result && noAnswerFound && (
          <div className="no-answer">
            <p>{stripDisclaimer(result.answer).trim()}</p>
          </div>
        )}

      </main>

      <footer className="disclaimer">
        <p className="container">
          {DISCLAIMER_LINE} For your specific situation, consult a licensed California attorney
          or the California Courts Self-Help Center.
        </p>
      </footer>

      {activeInfo && <InfoModal content={INFO_CONTENT[activeInfo]} onClose={() => setActiveInfo(null)} />}
    </div>
  );
}

export default App;
