"""
Fetch and locally cache the raw HTML for CA Civil Code Chapter 2 (Hiring of
Real Property), sections 1940-1954.071.

Why cache the raw HTML to disk: reproducibility. Every later step (parsing,
chunking, embedding) should be re-runnable against a fixed snapshot of the
source without re-hitting leginfo.legislature.ca.gov each time, and so we have
an auditable record of exactly what text the system was grounded on.
"""

from __future__ import annotations

from pathlib import Path

import requests

SOURCE_URL = (
    "https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml"
    "?lawCode=CIV&division=3.&title=5.&part=4.&chapter=2.&article="
)

RAW_HTML_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "civ_code_ch2.html"


def fetch_raw_html(force: bool = False) -> Path:
    """Download the chapter page to data/raw/ if not already cached."""
    if RAW_HTML_PATH.exists() and not force:
        return RAW_HTML_PATH

    RAW_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    RAW_HTML_PATH.write_text(resp.text, encoding="utf-8")
    return RAW_HTML_PATH


if __name__ == "__main__":
    path = fetch_raw_html()
    print(f"Raw HTML at {path} ({path.stat().st_size} bytes)")
