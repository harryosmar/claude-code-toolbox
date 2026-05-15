"""Chat orchestrator — keeps the ``stream_chat`` public symbol while routing
the actual LLM call through ``server.llm.factory.chat_port()``.

This module used to wrap the Anthropic SDK directly. Since 0.4.0 the
provider-specific logic lives in ``server.llm.adapters.anthropic_adapter``;
this file is now ~50 LOC of orchestration:

    1. Build the conversation: history + current user turn (string content).
    2. Call ``chat_port().stream_chat(...)`` with the port-shape inputs.
    3. Marshal ``ChatEvent`` instances back into the SSE-shaped dicts that
       ``server/api/chat.py`` and ``server/api/audit.py`` already consume.

The public signature is intentionally identical to pre-0.4.0 so the two
consumers (chat.py, audit.py) are zero-diff. The wire format of the emitted
SSE dicts (``token`` / ``citations`` / ``done``) is also byte-identical to
pre-0.4.0 — guaranteed by ``tests/test_chat_port_fidelity.py``.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from server.config import settings
from server.llm.factory import chat_port
from server.llm.ports import (
    ChatCitationEvent,
    ChatDoneEvent,
    ChatTokenEvent,
    LLMUsage,
    PortCitation,
)
from server.retrieval.search import Hit

log = logging.getLogger(__name__)


async def stream_chat(
    *,
    user_message: str,
    history: list[dict],
    documents: list[dict[str, Any]],
    system_prompt: str,
    hits: list[Hit],
) -> AsyncIterator[dict]:
    """Yield SSE-shaped events: token, citations, done.

    Same public contract as pre-0.4.0. ``history`` is the prior turns
    (excluding the current user_message). ``documents`` are the
    DocumentBlockParam dicts built by retrieval/prompt.py. ``hits`` lets
    us hydrate citations with their source_url after the stream.
    """
    port = chat_port()

    # Build messages in port shape: history + current user turn, content as
    # strings. The adapter splices ``documents`` into the last user turn.
    port_messages: list[dict] = list(history) + [
        {"role": "user", "content": user_message},
    ]

    raw_citations: list[PortCitation] = []  # buffered, hydrated post-stream
    answer_chars = 0
    final_usage: LLMUsage | None = None

    async for evt in port.stream_chat(
        system_prompt=system_prompt,
        cache_system_prompt=True,
        messages=port_messages,
        documents=documents,
        max_tokens=settings.chat_max_tokens,
        model=settings.chat_model,
        enable_adaptive_thinking=settings.chat_adaptive_thinking,
    ):
        if isinstance(evt, ChatTokenEvent):
            answer_chars += len(evt.text)
            yield {"type": "token", "text": evt.text}
        elif isinstance(evt, ChatCitationEvent):
            raw_citations.append(evt.citation)
        elif isinstance(evt, ChatDoneEvent):
            final_usage = evt.usage

    yield {
        "type": "citations",
        "citations": _hydrate(raw_citations, hits=hits),
    }

    # Flatten LLMUsage into the public SSE shape. The two Anthropic cache
    # counters are top-level inside ``usage`` (not nested) so the wire shape
    # stays byte-identical to pre-0.4.0 on the Anthropic path. Non-Anthropic
    # adapters populate provider_extras with their own counters (e.g.
    # OpenAI's ``prompt_cache_tokens``) — those don't surface on the wire
    # today; operators can read them off ``/health`` capability metadata
    # and the ``stream_chat`` return value for debugging.
    extras: dict[str, int] = final_usage.provider_extras if final_usage else {}
    caps = port.capabilities
    # citation_mode tells the widget how to render the source panel:
    # "native" — char-level citations present (Anthropic); the citations
    # list has hydrated entries with char_start/char_end offsets.
    # "unavailable" — adapter doesn't support citations; the list is empty
    # and the widget should show "sources hidden" or hide the panel.
    # New field in 0.5.0 — additive; widgets that ignore unknown fields
    # keep working unchanged.
    citation_mode = "native" if caps.supports_native_citations else "unavailable"
    yield {
        "type": "done",
        "usage": {
            "input_tokens": final_usage.input_tokens if final_usage else 0,
            "output_tokens": final_usage.output_tokens if final_usage else 0,
            "cache_creation_input_tokens": extras.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": extras.get("cache_read_input_tokens", 0),
        },
        "answer_chars": answer_chars,
        "model": settings.chat_model,
        "citation_mode": citation_mode,
    }


def _hydrate(raw: list[PortCitation], *, hits: list[Hit]) -> list[dict]:
    """Map port-shape citations back to the source chunk's metadata.

    Drops citations whose ``document_index`` is out-of-range for ``hits``
    (defensive — the adapter shouldn't emit those, but the API has been
    seen to). Dedups identical (doc, start, end) tuples that Anthropic
    sometimes emits twice during streaming.
    """
    out: list[dict] = []
    seen: set[tuple[int, int, int]] = set()
    for c in raw:
        if not (0 <= c.document_index < len(hits)):
            continue
        key = (c.document_index, c.start_char_index or 0, c.end_char_index or 0)
        if key in seen:
            continue
        seen.add(key)
        h = hits[c.document_index]
        out.append(
            {
                "index": len(out),
                "url": h.metadata.get("source_url", ""),
                "title": h.metadata.get("page_title", ""),
                "section_title": h.metadata.get("section_title", ""),
                "snippet": c.cited_text,
                "char_start": c.start_char_index,
                "char_end": c.end_char_index,
            }
        )
    return out


__all__ = ["stream_chat"]
