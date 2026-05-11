"""System prompt + DocumentBlockParam assembly for Claude with native Citations.

We hand Claude one document per retrieved chunk (rather than concatenating)
so the Citations API can return per-chunk char_location references that map
cleanly back to source URLs.

The SYSTEM prompt is rendered per request with the site's bilingual glossary
spliced in (see ``glossary.py``). Sites with no auto-extracted acronyms get
the base prompt unchanged, so behaviour is unchanged from the pre-glossary
path for any operator that hasn't ingested a site yet.
"""
from __future__ import annotations

from typing import Any

from server.retrieval.glossary import render_glossary_text
from server.retrieval.search import Hit


_BASE_SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions ONLY using the provided documents.

Behaviour:
- If the documents don't contain enough information to answer, say so plainly.
- Mirror the language of the user's question — answer in English when asked in English, in Bahasa Indonesia when asked in Bahasa Indonesia.
- Cite the documents you used. The citation system is automatic (the Citations API attaches per-claim references); you don't need to write [1] markers manually — just use the cited_text feature naturally.
- Be concise. Prefer 2–4 sentences plus bullet points when listing.
- Do not invent URLs, IDs, or numbers that aren't in the documents.
"""


_GLOSSARY_HEADER = """\

Corpus glossary — acronyms used throughout this site. When the user asks
about one of these in any language, you may use the expansion below as
ground truth even if the retrieved documents only mention the acronym in
passing. The Indonesian expansion is the authoritative source; the English
gloss in parentheses (where given) is a translation aid for cross-lingual
questions.

"""


SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT
"""Backwards-compatible module-level constant — equivalent to
``build_system_prompt(site_id=None)``. Prefer the function form for any
new caller so per-site glossary injection takes effect."""


def build_system_prompt(site_id: str | None) -> str:
    """Render the SYSTEM prompt for one request.

    When ``site_id`` matches a per-site acronym table that has any entries,
    the bilingual glossary is appended after the base behaviour rules. When
    the site has no auto-extracted acronyms (or no site_id is supplied), the
    base prompt is returned unchanged so behaviour stays identical to the
    pre-glossary path."""
    body = render_glossary_text(site_id)
    if not body:
        return _BASE_SYSTEM_PROMPT
    return _BASE_SYSTEM_PROMPT + _GLOSSARY_HEADER + body + "\n"


def build_documents(hits: list[Hit]) -> list[dict[str, Any]]:
    """Build Anthropic DocumentBlockParam list — one per retrieved chunk."""
    docs: list[dict[str, Any]] = []
    for i, h in enumerate(hits):
        title = h.metadata.get("page_title") or h.metadata.get("source_url") or f"chunk-{i}"
        docs.append(
            {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": h.text},
                "title": str(title)[:200],
                "context": str(h.metadata.get("source_url", "")),
                "citations": {"enabled": True},
            }
        )
    return docs


__all__ = ["SYSTEM_PROMPT", "build_system_prompt", "build_documents"]
