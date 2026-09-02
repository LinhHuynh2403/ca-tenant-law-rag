import { useState } from "react";
import type { CitationInfo } from "./api";

export function CitationCard({ citation }: { citation: CitationInfo }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="citation-card">
      <button
        type="button"
        className="citation-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="citation-caret">{open ? "▾" : "▸"}</span>
        {citation.citation}
      </button>
      {open && (
        <div className="citation-body">
          <p>{citation.text}</p>
          <a href={citation.source_url} target="_blank" rel="noreferrer">
            View official source ↗
          </a>
        </div>
      )}
    </div>
  );
}
