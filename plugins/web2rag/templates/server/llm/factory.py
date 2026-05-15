"""Port factory — selects which adapter implementation backs each port.

Keyed off ``settings.llm_provider`` (env: ``LLM_PROVIDER``). 0.4.0 ships
only ``"anthropic"``; 0.5.0 will add ``"openai-compat"``. Each factory is
``@lru_cache``-d so a single adapter instance is shared across the api's
lifetime (matching the singleton convention used by the embedder, reranker,
prompt-guard, and other heavy stateful components).
"""
from __future__ import annotations

import logging
from functools import lru_cache

from server.config import settings
from server.llm.ports import LLMChatPort, LLMRewritePort

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def chat_port() -> LLMChatPort:
    provider = settings.llm_provider
    if provider == "anthropic":
        from server.llm.adapters.anthropic_adapter import AnthropicChatAdapter
        return AnthropicChatAdapter()
    raise ValueError(
        f"unsupported LLM_PROVIDER={provider!r} for chat port. "
        "0.4.0 supports only 'anthropic'. 0.5.0 will add 'openai-compat'."
    )


@lru_cache(maxsize=1)
def rewrite_port() -> LLMRewritePort:
    provider = settings.llm_provider
    if provider == "anthropic":
        from server.llm.adapters.anthropic_adapter import AnthropicRewriteAdapter
        return AnthropicRewriteAdapter()
    raise ValueError(
        f"unsupported LLM_PROVIDER={provider!r} for rewrite port. "
        "0.4.0 supports only 'anthropic'. 0.5.0 will add 'openai-compat'."
    )


__all__ = ["chat_port", "rewrite_port"]
