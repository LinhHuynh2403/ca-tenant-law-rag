import type { CitationInfo } from "./api";

const SNIPPET_LIMIT = 220;

function truncate(text: string, limit: number): string {
  if (text.length <= limit) return text;
  return text.slice(0, limit).trimEnd() + "…";
}

export function CitationCard({ citation }: { citation: CitationInfo }) {
  return (
    <a
      className="citation-card"
      href={citation.source_url}
      target="_blank"
      rel="noreferrer"
      title={citation.text}
    >
      <span className="citation-badge">§ {citation.section_number}</span>
      <span className="citation-body">
        <span className="citation-name">{citation.citation}</span>
        <span className="citation-snippet">{truncate(citation.text, SNIPPET_LIMIT)}</span>
      </span>
      <span className="citation-arrow" aria-hidden="true">
        ↗
      </span>
    </a>
  );
}
