import { useState, type FormEvent } from "react";
import { askQuestion, type AskResponse } from "./api";
import { CitationCard } from "./CitationCard";
import "./App.css";

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
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <header>
        <h1>CA Tenant Law Q&amp;A</h1>
        <p className="subtitle">
          Answers come only from the California Civil Code, §§1940–1954.071 (Hiring of Real
          Property), and cite the exact section for every claim.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="ask-form">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. How many days does a landlord have to return my security deposit?"
          rows={3}
        />
        <button type="submit" disabled={loading || !question.trim()}>
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>

      {error && <div className="banner banner-error">{error}</div>}

      {result && (
        <div className="answer-block">
          {!result.is_fully_grounded && (
            <div className="banner banner-warning">
              One or more citations in this answer could not be verified against the retrieved
              sources.
            </div>
          )}

          <div className="answer-text">
            {result.answer
              .split("\n")
              .filter((line) => line.trim().length > 0)
              .map((line, i) => (
                <p key={i}>{line}</p>
              ))}
          </div>

          {result.citations.length > 0 && (
            <div className="sources">
              <h2>Sources ({result.citations.length})</h2>
              {result.citations.map((c) => (
                <CitationCard key={c.citation} citation={c} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default App;
