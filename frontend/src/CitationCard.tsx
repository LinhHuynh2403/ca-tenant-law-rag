import type { CitationInfo } from "./api";

const EXCERPT_LIMIT = 240;

function truncate(text: string, limit: number): string {
  if (text.length <= limit) return text;
  return text.slice(0, limit).trimEnd() + "…";
}

export function CitationCard({ citation, index }: { citation: CitationInfo; index: number }) {
  return (
    <a
      id={`source-${index}`}
      className="citation-card"
      href={citation.source_url}
      target="_blank"
      rel="noreferrer"
    >
      <span className="citation-number">{index}</span>
      <span className="citation-main">
        <span className="citation-top-row">
          <span className="citation-name">{citation.citation}</span>
          <span className="citation-external" aria-hidden="true">
            ↗
          </span>
        </span>
        {citation.label && <span className="citation-label">{citation.label}</span>}
        <blockquote className="citation-excerpt">{truncate(citation.text, EXCERPT_LIMIT)}</blockquote>
      </span>
    </a>
  );
}
