"""System prompt + DocumentBlockParam assembly for Claude with native Citations.

We hand Claude one document per retrieved chunk (rather than concatenating)
so the Citations API can return per-chunk char_location references that map
cleanly back to source URLs.
"""
from __future__ import annotations

from typing import Any

from server.retrieval.search import Hit


SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions ONLY using the provided documents.

Behaviour:
- If the documents don't contain enough information to answer, say so plainly.
- Mirror the language of the user's question — answer in English when asked in English, in Bahasa Indonesia when asked in Bahasa Indonesia.
- Cite the documents you used. The citation system is automatic (the Citations API attaches per-claim references); you don't need to write [1] markers manually — just use the cited_text feature naturally.
- Be concise. Prefer 2–4 sentences plus bullet points when listing.
- Do not invent URLs, IDs, or numbers that aren't in the documents.
"""


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
