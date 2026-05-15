"""Port factory — selects which adapter implementation backs each port.

Provider resolution honors split-per-port env knobs:

  - ``LLM_CHAT_PROVIDER``     overrides which adapter backs ``chat_port()``.
  - ``LLM_REWRITE_PROVIDER``  overrides which adapter backs ``rewrite_port()``.
  - ``LLM_PROVIDER``          fallback when the per-port override is empty.

This lets operators run mixed-provider configs — e.g. chat on a local
vLLM (cost optimization) but rewrite still on Anthropic Haiku (rewrite is
~$0.0001/turn, cheaper to keep on a managed API than to add latency to the
local stack). The split was the rationale for shipping two adapter classes
per provider in 0.4.0 instead of one class implementing both ports.

Each factory is ``@lru_cache``-d so a single adapter instance is shared
across the api's lifetime (matching the singleton convention used by the
embedder, reranker, prompt-guard, etc.).
"""
from __future__ import annotations

import logging
from functools import lru_cache

from server.config import settings
from server.llm.ports import LLMChatPort, LLMRewritePort

log = logging.getLogger(__name__)


def _resolve_chat_provider() -> str:
    """Per-port override wins; otherwise fall back to the global LLM_PROVIDER."""
    return (settings.llm_chat_provider or "").strip() or settings.llm_provider


def _resolve_rewrite_provider() -> str:
    return (settings.llm_rewrite_provider or "").strip() or settings.llm_provider


@lru_cache(maxsize=1)
def chat_port() -> LLMChatPort:
    provider = _resolve_chat_provider()
    if provider == "anthropic":
        from server.llm.adapters.anthropic_adapter import AnthropicChatAdapter
        return AnthropicChatAdapter()
    if provider == "openai-compat":
        from server.llm.adapters.openai_compat_adapter import OpenAICompatChatAdapter
        return OpenAICompatChatAdapter()
    raise ValueError(
        f"unsupported LLM provider {provider!r} for chat port. "
        "Supported: 'anthropic', 'openai-compat'."
    )


@lru_cache(maxsize=1)
def rewrite_port() -> LLMRewritePort:
    provider = _resolve_rewrite_provider()
    if provider == "anthropic":
        from server.llm.adapters.anthropic_adapter import AnthropicRewriteAdapter
        return AnthropicRewriteAdapter()
    if provider == "openai-compat":
        from server.llm.adapters.openai_compat_adapter import OpenAICompatRewriteAdapter
        return OpenAICompatRewriteAdapter()
    raise ValueError(
        f"unsupported LLM provider {provider!r} for rewrite port. "
        "Supported: 'anthropic', 'openai-compat'."
    )


__all__ = ["chat_port", "rewrite_port"]
