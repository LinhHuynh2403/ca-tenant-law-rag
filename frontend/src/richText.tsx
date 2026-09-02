import type { ReactNode } from "react";
import type { CitationInfo } from "./api";

// Matches generation/generate.py's SYSTEM_PROMPT output format exactly:
// citations as [[Cal. Civ. Code § ...]] tags, and at most the single opening
// figure wrapped in **bold**. Keeping this in sync with the backend format
// is what makes the footnote-number rendering below possible without a
// markdown library -- we're parsing a format we control, not arbitrary
// markdown.
const MARKUP_RE = /\[\[(.*?)\]\]|\*\*(.*?)\*\*/g;

/**
 * Renders one line of answer text, converting:
 *   [[citation]] -> a numbered, clickable footnote (linking to the matching
 *                   citation's source card), numbered by that citation's
 *                   position in `citations` (its first-appearance order).
 *   **text**     -> <strong>text</strong>
 * Anything that doesn't match either pattern is rendered as plain text.
 */
export function renderRichText(line: string, citations: CitationInfo[]): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let matchCount = 0;

  for (const match of line.matchAll(MARKUP_RE)) {
    const [full, citationText, boldText] = match;
    const index = match.index ?? 0;

    if (index > lastIndex) {
      nodes.push(line.slice(lastIndex, index));
    }

    if (citationText !== undefined) {
      const sourceIndex = citations.findIndex((c) => c.citation === citationText);
      if (sourceIndex !== -1) {
        nodes.push(
          <sup key={`cite-${index}`} className="footnote">
            <a href={`#source-${sourceIndex + 1}`}>{sourceIndex + 1}</a>
          </sup>,
        );
      }
      // If the citation isn't found among the resolved sources, drop it
      // silently rather than show a broken/dangling tag -- generate_answer()
      // already guarantees every citation in the answer is grounded
      // (is_fully_grounded), so this branch is a defensive no-op in
      // practice, not an expected path.
    } else if (boldText !== undefined) {
      nodes.push(<strong key={`bold-${index}`}>{boldText}</strong>);
    }

    lastIndex = index + full.length;
    matchCount++;
  }

  if (lastIndex < line.length) {
    nodes.push(line.slice(lastIndex));
  }

  return matchCount > 0 ? nodes : [line];
}
