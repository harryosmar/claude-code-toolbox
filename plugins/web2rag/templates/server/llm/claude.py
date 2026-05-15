"""Claude chat — Anthropic SDK + native Citations API + SSE streaming.

This is the ONLY chat-LLM path in the api. There's no Ollama fallback for
chat: that simplification removes the need for manual citation extraction
and keeps the system prompt + response shape uniform.

The eval-judge factory (eval/judges/factory.py) does support remote Ollama,
but that's a separate code path used only by /audit.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from server.config import settings
from server.retrieval.search import Hit

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    """Module-level AsyncAnthropic singleton.

    Why: each AsyncAnthropic() builds its own httpx pool — re-creating it
    per /chat request burns a TLS handshake per turn and discards any
    HTTP/2 connection reuse. Lazy via lru_cache so the empty-API-key case
    is still caught by chat.py's 503 gate before construction runs.
    """
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


async def stream_chat(
    *,
    user_message: str,
    history: list[dict],
    documents: list[dict[str, Any]],
    system_prompt: str,
    hits: list[Hit],
) -> AsyncIterator[dict]:
    """Yield SSE-shaped events: token, citations, done.

    `history` is the prior turns (excluding the current user_message).
    `documents` are the DocumentBlockParam list built by retrieval/prompt.py.
    `hits` lets us hydrate citations with their source_url after the stream.
    """
    client = _client()

    # Combine documents + the user's question as one user-turn content array.
    messages: list[dict] = list(history) + [
        {
            "role": "user",
            "content": [
                *documents,
                {"type": "text", "text": user_message},
            ],
        }
    ]

    answer_chars = 0
    raw_citations: list[dict] = []  # accumulated, returned after streaming

    async with client.messages.stream(
        model=settings.chat_model,
        max_tokens=settings.chat_max_tokens,
        system=system_prompt,
        messages=messages,
    ) as stream:
        async for event in stream:
            t = getattr(event, "type", None)
            if t == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                # text deltas → token events; citation deltas → buffered for the citations event.
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    text = getattr(delta, "text", "") or ""
                    if text:
                        answer_chars += len(text)
                        yield {"type": "token", "text": text}
                elif dtype == "citations_delta":
                    citation = getattr(delta, "citation", None)
                    if citation is None:
                        continue
                    raw_citations.append(_serialise_citation(citation))

        final = await stream.get_final_message()

    yield {
        "type": "citations",
        "citations": _hydrate(raw_citations, hits=hits),
    }
    yield {
        "type": "done",
        "usage": {
            "input_tokens": getattr(getattr(final, "usage", None), "input_tokens", 0),
            "output_tokens": getattr(getattr(final, "usage", None), "output_tokens", 0),
        },
        "answer_chars": answer_chars,
        "model": settings.chat_model,
    }


def _serialise_citation(citation: Any) -> dict:
    """Anthropic citation objects use camelCase via SDK; flatten to plain dict."""
    return {
        "type": getattr(citation, "type", None),
        "cited_text": getattr(citation, "cited_text", ""),
        "document_index": getattr(citation, "document_index", -1),
        "document_title": getattr(citation, "document_title", ""),
        "start_char_index": getattr(citation, "start_char_index", None),
        "end_char_index": getattr(citation, "end_char_index", None),
    }


def _hydrate(raw: list[dict], *, hits: list[Hit]) -> list[dict]:
    """Map Claude's document_index back to the source chunk's metadata."""
    out: list[dict] = []
    seen: set[tuple[int, int, int]] = set()
    for c in raw:
        idx = c.get("document_index", -1)
        if not (0 <= idx < len(hits)):
            continue
        # de-dup identical (doc, span) tuples Anthropic sometimes emits twice
        key = (idx, c.get("start_char_index") or 0, c.get("end_char_index") or 0)
        if key in seen:
            continue
        seen.add(key)
        h = hits[idx]
        out.append(
            {
                "index": len(out),
                "url": h.metadata.get("source_url", ""),
                "title": h.metadata.get("page_title", ""),
                "section_title": h.metadata.get("section_title", ""),
                "snippet": c.get("cited_text", ""),
                "char_start": c.get("start_char_index"),
                "char_end": c.get("end_char_index"),
            }
        )
    return out
