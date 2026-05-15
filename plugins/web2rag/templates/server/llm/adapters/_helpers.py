"""Cross-adapter helpers — small utilities shared across LLM adapters.

Currently:

  - ``documents_to_text_block(documents)`` — flattens the canonical
    DocumentBlockParam shape into a stylized ``<context>...</context>`` text
    block. Used by adapters that lack a native document-block primitive
    (OpenAI / vLLM / Ollama / etc.) to inject retrieved chunks into the
    system prompt instead.

Kept deliberately tiny — anything provider-specific belongs in the adapter,
not here. This module exists because the Anthropic DocumentBlockParam shape
happens to be the canonical port input (Anthropic is the lead adapter), so
non-Anthropic adapters all share the same flattening rule.
"""
from __future__ import annotations

from typing import Iterable


def documents_to_text_block(documents: Iterable[dict]) -> str:
    """Convert canonical DocumentBlockParam dicts to a single text block.

    Each input dict is expected to look like::

        {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": "..."},
            "title": "Page title or chunk-N",
            "context": "https://...",       # optional — the source URL
            "citations": {"enabled": True}, # ignored here
        }

    Output is a single string formatted as::

        <context>
        [1] Title — https://...
        full text...

        [2] Title — https://...
        full text...
        </context>

    Empty / missing documents collapse to an empty string so the caller
    can splice it into the system prompt unconditionally.
    """
    docs = [d for d in documents if d]
    if not docs:
        return ""

    parts: list[str] = ["<context>"]
    for i, d in enumerate(docs, start=1):
        title = (d.get("title") or "").strip() or f"chunk-{i}"
        source = d.get("source") or {}
        # source.data is the chunk's raw text under Anthropic's text-document
        # shape. Adapters that get a different shape (e.g. base64 PDFs) would
        # fall through here as "" — non-Anthropic backends don't get PDFs.
        body = (source.get("data") or "") if isinstance(source, dict) else ""
        url = (d.get("context") or "").strip()
        header = f"[{i}] {title}" + (f" — {url}" if url else "")
        parts.append(header)
        parts.append(body.strip())
        parts.append("")  # blank line between docs
    parts.append("</context>")
    return "\n".join(parts).rstrip() + "\n"


__all__ = ["documents_to_text_block"]
