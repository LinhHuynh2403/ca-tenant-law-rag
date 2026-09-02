import { useState, type FormEvent } from "react";
import { askQuestion, type AskResponse } from "./api";
import { CitationCard } from "./CitationCard";
import "./App.css";

const DISCLAIMER_LINE = "This is general information, not legal advice.";

// The backend appends this line to every answer (see generation/generate.py's
// system prompt). The footer shows a permanent disclaimer already, so strip
// the inline copy rather than show it twice.
function stripDisclaimer(answer: string): string {
  return answer
    .split("\n")
    .filter((line) => line.trim() !== DISCLAIMER_LINE)
    .join("\n");
}

function App() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = question.trim();
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

  // A refusal always carries zero citations (enforced by the generation
  // prompt) -- that's the reliable signal for "no answer found," distinct
  // from a network/API failure.
  const noAnswerFound = result !== null && result.citations.length === 0;

  return (
    <div className="page">
      <header className="site-header">
        <h1>CA Tenant Law Q&amp;A</h1>
        <p className="subtitle">
          Answers are grounded only in the California Civil Code, §§1940–1954.071 (Hiring of
          Real Property) — every claim traces to a real, clickable source.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="ask-form">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. How many days does a landlord have to return my security deposit?"
          rows={3}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !question.trim()}>
          {loading ? "Reading the statute…" : "Ask"}
        </button>
      </form>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      {result && !noAnswerFound && (
        <div className="answer-block">
          {!result.is_fully_grounded && (
            <div className="banner banner-warning">
              One or more citations in this answer could not be verified against the retrieved
              sources.
            </div>
          )}

          <div className="answer-text">
            {stripDisclaimer(result.answer)
              .split("\n")
              .filter((line) => line.trim().length > 0)
              .map((line, i) => (
                <p key={i}>{line}</p>
              ))}
          </div>

          <div className="sources">
            <h2>Sources</h2>
            <div className="citation-list">
              {result.citations.map((c) => (
                <CitationCard key={c.citation} citation={c} />
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

      <footer className="disclaimer">{DISCLAIMER_LINE}</footer>
    </div>
  );
}

export default App;
