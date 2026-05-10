"""Per-page text normalisation — corpus-agnostic boilerplate stripping.

Runs after `guard_ingest` and before `chunk_markdown`. The goal is to
remove noise that the HTML→markdown converter and the source CMS introduce
without removing real content. All rules are universal — no per-site
patterns, no domain-specific keywords, no language assumptions beyond
"this text is approximately Unicode-segmented prose."

What it does (each step idempotent and language-agnostic):

  1. **Decorative-separator lines.** Lines that are *only* punctuation
     and whitespace ("/  /  /  /", "==========", "—————", "* * *",
     "···") get dropped. These are HTML breadcrumb separators, theme
     dividers, or list-marker residue. Real content always contains at
     least one alphanumeric character per visible line.

  2. **Collapse blank runs.** 3+ consecutive blank lines → 1. Keeps
     paragraph breaks visible to the chunker (which uses "\\n\\n" as a
     splitter) without letting decorative whitespace inflate token counts.

  3. **Trim trailing whitespace per line.** Cosmetic, but keeps chunk
     hashes stable across cosmetic re-renders of the same content.

What it does NOT do (deliberately out of scope for the per-page pass):

  - Cross-page boilerplate detection (e.g. "every page on this site has
    the same 50-line footer"). That's a corpus-level pass; doing it here
    would require buffering the whole crawl. A future enhancement.
  - Language-specific cleanup (Indonesian-specific abbreviations, Chinese
    full-width punctuation, etc.) — the rules above are intentionally
    universal so a fresh deployment crawling docs.example.com / kemenkes
    / bgn / wikipedia all benefit equally.
  - HTML stripping — the scraper/converter has already done that. We
    operate on the markdown the guard_ingest produced.

Why this fixes the FAQ-vs-article ranking issue we saw on BGN:
  Every FAQ chunk used to start with the boilerplate prefix
  `"### FAQ BGN /  /  # Kupas Tuntas..."`. The slashes carried no signal
  but ate ~40 chars of every chunk's embedding budget — diluting the
  semantic match against any query. After normalisation, those slashes
  are gone and FAQ chunks embed closer to the actual Q&A content.
"""
from __future__ import annotations

import re

# A "decorative-separator" line is one that contains zero alphanumeric or
# CJK characters — i.e. nothing a human would read as content. Punctuation,
# whitespace, and standalone symbols pass through.
#
# `\w` in Python's `re` defaults to Unicode-aware matching (catches Latin,
# Cyrillic, Greek, Arabic letter classes). CJK ideographs are matched via
# the explicit unicode ranges below — `\w` doesn't cover them in default
# Python re mode, only in `regex` library.
_HAS_LETTER_RE = re.compile(
    r"[\w"                      # Unicode word chars (Latin/Cyrillic/Arabic/Greek)
    r"一-鿿"            # CJK Unified Ideographs (most common Chinese)
    r"぀-ゟ"            # Hiragana
    r"゠-ヿ"            # Katakana
    r"가-힯"            # Hangul Syllables
    r"]"
)

# Match runs of 2+ blank lines (whitespace-only). Replacement is a single
# blank line, preserving paragraph structure for the chunker.
_BLANK_RUN_RE = re.compile(r"(?:[ \t]*\n){3,}")


def _is_decorative_line(line: str) -> bool:
    """A line counts as decorative when it has no readable letters."""
    return _HAS_LETTER_RE.search(line) is None


def normalize(text: str) -> str:
    """Strip decorative noise and collapse blank runs. Idempotent: running
    normalize() over already-normalised text returns identical output."""
    if not text:
        return text

    lines = text.splitlines()
    out_lines = []
    for line in lines:
        stripped = line.rstrip()
        if _is_decorative_line(stripped):
            # Replace with a true blank line (no content) — preserves
            # paragraph spacing without keeping the decorative residue.
            out_lines.append("")
        else:
            out_lines.append(stripped)

    rejoined = "\n".join(out_lines)
    # Collapse runs of 3+ blank lines into a single blank-line separator.
    rejoined = _BLANK_RUN_RE.sub("\n\n", rejoined)
    return rejoined.strip("\n") + "\n" if rejoined.strip() else ""


__all__ = ["normalize"]
