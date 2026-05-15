"""Anthropic SDK adapter — implements ``LLMChatPort`` and ``LLMRewritePort``.

Full feature parity:

  - Native Citations API via ``citations_delta`` events with char-level offsets
  - DocumentBlockParam shape passed through unchanged
  - Opt-in adaptive thinking gated by an audited model registry
  - Prompt cache_control on the system block when the orchestrator asks for it

Two adapter classes (one per port) rather than one class implementing both —
this lets operators in 0.5.0 mix providers per-port (chat on local vLLM,
rewrite on Anthropic Haiku). The classes share module-level client
singletons via ``@lru_cache`` so there is no per-instance state to manage.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from server.config import settings
from server.llm.ports import (
    ChatCitationEvent,
    ChatDoneEvent,
    ChatEvent,
    ChatTokenEvent,
    LLMCapabilities,
    LLMUsage,
    PortCitation,
)

log = logging.getLogger(__name__)


# ─── module-level client singletons ──────────────────────────────────────────
# One AsyncAnthropic per adapter context. Lazy via lru_cache so the
# empty-API-key case is still gated by the api's 503 check at the chat.py
# entry point — construction doesn't run until the first /chat call.
#
# NOT a method-bound lru_cache: lru_cache on instance methods retains `self`
# in the cache key and defeats GC. The module-level wrapper is the safe
# pattern.


@lru_cache(maxsize=1)
def _get_chat_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


@lru_cache(maxsize=1)
def _get_rewrite_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


# ─── adaptive-thinking model registry ────────────────────────────────────────
# Moved verbatim from server/llm/claude.py (was added in 0.3.0). Static
# frozenset — not startswith/regex — so a future model ID we haven't tested
# against can't silently opt-in. Haiku 4.5 is excluded: it 400s on adaptive
# thinking, and the cost/latency profile of Haiku is the wrong target for a
# thinking budget anyway. Bump this set when Anthropic ships new
# Claude 4.6+ models that support adaptive thinking.

_ADAPTIVE_THINKING_MODELS: frozenset[str] = frozenset({
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
})


def _adaptive_enabled_for(model: str) -> bool:
    return model in _ADAPTIVE_THINKING_MODELS


# ─── capability dataclasses (module-level constants, shared across instances) ─


_CHAT_CAPABILITIES = LLMCapabilities(
    supports_streaming=True,
    supports_native_citations=True,
    supports_document_blocks=True,
    supports_adaptive_thinking=True,
    supports_prompt_cache_control=True,
)


_REWRITE_CAPABILITIES = LLMCapabilities(
    # Rewrite uses messages.create (non-streaming), no docs, no citations,
    # and a short prompt that won't reach the cacheable-prefix minimum.
    supports_streaming=False,
    supports_native_citations=False,
    supports_document_blocks=False,
    supports_adaptive_thinking=False,
    supports_prompt_cache_control=False,
)


# ─── chat adapter ────────────────────────────────────────────────────────────


class AnthropicChatAdapter:
    """Streams chat completions through ``messages.stream`` with full
    Citations API / cache_control / adaptive thinking support."""

    @property
    def capabilities(self) -> LLMCapabilities:
        return _CHAT_CAPABILITIES

    async def stream_chat(
        self,
        *,
        system_prompt: str,
        cache_system_prompt: bool,
        messages: list[dict],
        documents: list[dict],
        max_tokens: int,
        model: str,
        enable_adaptive_thinking: bool,
    ) -> AsyncIterator[ChatEvent]:
        client = _get_chat_client()

        # Splice documents into the last (current) user turn's content. The
        # orchestrator hands us ``messages`` with string content; we convert
        # the final user turn into a content array of
        # ``[*documents, {"type": "text", "text": <user_text>}]``.
        sdk_messages: list[dict] = list(messages[:-1])
        if messages:
            last = messages[-1]
            last_content = last.get("content", "")
            last_text = last_content if isinstance(last_content, str) else ""
            sdk_messages.append(
                {
                    "role": last.get("role", "user"),
                    "content": [
                        *documents,
                        {"type": "text", "text": last_text},
                    ],
                }
            )

        # System block — list with cache_control when the orchestrator asks.
        # Below the model's minimum cacheable prefix the API silently
        # no-caches; the marker is a free option that pays off as soon as
        # the system prompt grows past the threshold.
        if cache_system_prompt:
            system_payload: list[dict] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_payload = [{"type": "text", "text": system_prompt}]

        stream_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_payload,
            "messages": sdk_messages,
        }

        # Opt-in adaptive thinking, gated on both the operator flag AND the
        # model registry. Pre-checked instead of try/catching a 400 because
        # Haiku 4.5 rejects this param — a blind try would burn one failed
        # roundtrip per request on the default chat model.
        if enable_adaptive_thinking and _adaptive_enabled_for(model):
            stream_kwargs["thinking"] = {"type": "adaptive"}

        async with client.messages.stream(**stream_kwargs) as stream:
            async for event in stream:
                t = getattr(event, "type", None)
                if t != "content_block_delta":
                    continue
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                # text_delta → token; citations_delta → citation event;
                # thinking_delta deliberately dropped (the widget displays
                # answers, not reasoning traces).
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    text = getattr(delta, "text", "") or ""
                    if text:
                        yield ChatTokenEvent(text=text)
                elif dtype == "citations_delta":
                    raw = getattr(delta, "citation", None)
                    if raw is None:
                        continue
                    yield ChatCitationEvent(
                        citation=PortCitation(
                            document_index=getattr(raw, "document_index", -1),
                            cited_text=getattr(raw, "cited_text", "") or "",
                            start_char_index=getattr(raw, "start_char_index", None),
                            end_char_index=getattr(raw, "end_char_index", None),
                        )
                    )

            final = await stream.get_final_message()

        # Map the SDK's usage into LLMUsage. Anthropic-specific counters
        # (cache_creation / cache_read) go into provider_extras so the
        # orchestrator can flatten them into the public SSE wire shape
        # without polluting LLMUsage with provider-specific fields.
        final_usage_obj = getattr(final, "usage", None)
        extras: dict[str, int] = {}
        if final_usage_obj is not None:
            cc = getattr(final_usage_obj, "cache_creation_input_tokens", 0) or 0
            cr = getattr(final_usage_obj, "cache_read_input_tokens", 0) or 0
            extras["cache_creation_input_tokens"] = int(cc)
            extras["cache_read_input_tokens"] = int(cr)

        yield ChatDoneEvent(
            usage=LLMUsage(
                input_tokens=int(getattr(final_usage_obj, "input_tokens", 0) or 0) if final_usage_obj else 0,
                output_tokens=int(getattr(final_usage_obj, "output_tokens", 0) or 0) if final_usage_obj else 0,
                provider_extras=extras,
            )
        )


# ─── rewrite adapter ─────────────────────────────────────────────────────────


class AnthropicRewriteAdapter:
    """Non-streaming completion via ``messages.create``. Used to
    decontextualize multi-turn user queries before retrieval."""

    @property
    def capabilities(self) -> LLMCapabilities:
        return _REWRITE_CAPABILITIES

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        model: str,
    ) -> str:
        client = _get_rewrite_client()
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Anthropic's content is a list of blocks; we want concatenated text.
        # The current rewrite path is built on the same pattern in rewrite.py.
        parts: list[str] = []
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", "") or "")
        return "".join(parts).strip()


__all__ = ["AnthropicChatAdapter", "AnthropicRewriteAdapter"]
