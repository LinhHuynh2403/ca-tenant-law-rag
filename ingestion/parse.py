"""
Parse the raw leginfo.ca.gov HTML for CA Civil Code Chapter 2 into structured,
hierarchy-preserving records (StatuteSection / Subsection, see models.py).

Approach
--------
leginfo renders each code section as a flat sequence of <p> tags: one <h6><a>
holding the section number, followed by one <p> per subsection paragraph.
Subsection nesting is NOT expressed as real HTML nesting -- every paragraph is
a sibling. But leginfo does encode depth visually via each paragraph's
`margin-left` CSS, on a clean, consistent scale across the whole document:

    no margin-left -> depth 0   (a), (b), (c) ...
    margin-left: 1em   -> depth 1   (1), (2), (3) ...
    margin-left: 2.5em -> depth 2   (A), (B), (C) ...
    margin-left: 4em   -> depth 3   (i), (ii), (iii) ...
    margin-left: 5.5em -> depth 4   (I), (II), (III) ...
    margin-left: 7em   -> depth 5   (further nesting, rare)

This CSS depth is the ground truth we build the tree from. It matters because
the marker text alone is genuinely ambiguous: a single letter like "c", "d",
"i", "v", "x" is simultaneously a valid next-lowercase-letter AND a valid
roman numeral, and there is no way to tell which was meant from the character
alone -- e.g. "(i)" can be top-level subsection (i) (sibling of (h)) or the
first roman-numeral item nested three levels deep, and both readings are
locally "valid". An earlier version of this parser tried to resolve that from
marker grammar alone (sequence continuity) and silently mis-nested real
subsections as a result. Trusting the source's own layout signal instead of
re-deriving structure from marker shape avoids that failure mode entirely.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag

from ingestion.fetch import RAW_HTML_PATH, SOURCE_URL
from ingestion.models import Chapter, StatuteSection, Subsection

CODE_ABBREVIATION = "Cal. Civ. Code"

EM_TO_DEPTH = {"1": 1, "2.5": 2, "4": 3, "5.5": 4, "7": 5}
MARGIN_LEFT_RE = re.compile(r"margin-left:\s*([\d.]+)em")

# A token only counts as a subsection marker if it has one of these shapes.
# This excludes incidental parenthesized text in statute body copy, e.g. a
# notice template containing the literal text "(date)" as a fill-in blank.
_MARKER_SHAPE_RE = re.compile(r"^(?:[a-zA-Z]|\d+|[ivxlcdm]{2,}|[IVXLCDM]{2,})$")
LEAD_MARKER_RE = re.compile(r"^\(([a-zA-Z0-9]+)\)\s*")


def _css_depth(style: str) -> int:
    m = MARGIN_LEFT_RE.search(style)
    if not m:
        return 0
    return EM_TO_DEPTH.get(m.group(1), 0)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _StackFrame:
    __slots__ = ("depth", "node")

    def __init__(self, depth: int, node: Subsection):
        self.depth = depth
        self.node = node


def _parse_section_body(
    paragraphs: list[tuple[str, str]], section_number: str, warnings: list[str]
) -> tuple[str, list[Subsection]]:
    """Turn a section's ordered (style, text) content paragraphs into (intro_text, subsection tree)."""
    intro_parts: list[str] = []
    stack: list[_StackFrame] = []
    top_level: list[Subsection] = []

    for style, para in paragraphs:
        remaining = para
        leading_tokens: list[str] = []
        while True:
            m = LEAD_MARKER_RE.match(remaining)
            if not m or not _MARKER_SHAPE_RE.match(m.group(1)):
                break
            leading_tokens.append(m.group(1))
            remaining = remaining[m.end():]

        if not leading_tokens:
            if stack:
                # Continuation text with no marker of its own: append to the
                # deepest currently-open node rather than dropping it.
                stack[-1].node.text = _normalize_ws(stack[-1].node.text + " " + para)
            else:
                intro_parts.append(para)
            continue

        # The paragraph's own margin-left gives the depth of its FIRST marker.
        # A paragraph can open a compound chain of markers at once, e.g.
        # "(c) (1) text" where (c) itself carries no independent text --
        # each subsequent token in the chain is exactly one level deeper.
        base_depth = _css_depth(style)
        if stack and base_depth > stack[-1].depth + 1:
            warnings.append(
                f"§ {section_number}: paragraph depth {base_depth} jumps more than "
                f"one level past current depth {stack[-1].depth} (marker '({leading_tokens[0]})') "
                f"-- tree may be wrong here, please review."
            )

        for i, token in enumerate(leading_tokens):
            is_last = i == len(leading_tokens) - 1
            depth = base_depth + i
            stack = stack[:depth]

            path_parts = [f.node.label for f in stack] + [token]
            subsection_path = "".join(f"({p})" for p in path_parts)
            node = Subsection(
                label=token,
                level=depth,
                citation=f"{CODE_ABBREVIATION} § {section_number}{subsection_path}",
                subsection_path=subsection_path,
                text=_normalize_ws(remaining) if is_last else "",
                subsections=[],
            )

            if stack:
                stack[-1].node.subsections.append(node)
            else:
                top_level.append(node)
            stack.append(_StackFrame(depth, node))

    return _normalize_ws(" ".join(intro_parts)), top_level


def parse_chapter(html: str | None = None) -> Chapter:
    if html is None:
        html = RAW_HTML_PATH.read_text(encoding="utf-8")

    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", id="manylawsections")
    if container is None:
        raise ValueError("Could not find #manylawsections in the fetched HTML -- page structure may have changed.")

    division_heading = _normalize_ws(soup.find("h4").get_text(" ")) if soup.find("h4") else ""
    h4s = container.find_all("h4")
    part_heading = _normalize_ws(h4s[1].get_text(" ")) if len(h4s) > 1 else ""
    title_heading = _normalize_ws(h4s[2].get_text(" ")) if len(h4s) > 2 else ""
    h5 = container.find("h5")
    chapter_heading = _normalize_ws(h5.get_text(" ")) if h5 else ""

    sections: list[StatuteSection] = []
    warnings: list[str] = []
    current_number: str | None = None
    current_paragraphs: list[tuple[str, str]] = []
    current_history: list[str] = []

    def flush():
        if current_number is None:
            return
        intro_text, subsections = _parse_section_body(current_paragraphs, current_number, warnings)
        sections.append(
            StatuteSection(
                citation=f"{CODE_ABBREVIATION} § {current_number}",
                section_number=current_number,
                heading=None,
                jurisdiction="CA",
                source_url=SOURCE_URL,
                intro_text=intro_text,
                subsections=subsections,
                legislative_history=" ".join(current_history) or None,
            )
        )

    for el in container.find_all(["h6", "p"]):
        if el.name == "h6":
            flush()
            a = el.find("a")
            href = a["href"] if a else ""
            m = re.search(r"submitCodesValues\('\[?([\d.]+)\]?'", href)
            current_number = m.group(1).rstrip(".") if m else _normalize_ws(el.get_text())
            current_paragraphs = []
            current_history = []
            continue

        assert isinstance(el, Tag)
        style = el.get("style", "")
        if not style:
            # Outer wrapper <p> that (due to leginfo's invalid nested-<p> markup)
            # contains the entire section as descendant text -- skip to avoid
            # duplicating every paragraph inside it.
            continue

        text = _normalize_ws(el.get_text(" "))
        if not text:
            continue  # spacer <p .../> between paragraphs

        if "font-size:0.9em" in style:
            current_history.append(text)
        else:
            current_paragraphs.append((style, text))

    flush()

    for w in warnings:
        print(f"WARNING: {w}")

    return Chapter(
        division_heading=division_heading,
        part_heading=part_heading,
        title_heading=title_heading,
        chapter_heading=chapter_heading,
        source_url=SOURCE_URL,
        sections=sections,
    )


if __name__ == "__main__":
    chapter = parse_chapter()
    out_path = Path(__file__).resolve().parent.parent / "data" / "processed" / "civ_code_ch2_sections.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(chapter.model_dump_json(indent=2), encoding="utf-8")
    print(f"Parsed {len(chapter.sections)} sections -> {out_path}")
