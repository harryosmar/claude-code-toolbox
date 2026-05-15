"""OpenAI-compatible adapter — implements ``LLMChatPort`` + ``LLMRewritePort``.

Targets any backend that speaks OpenAI's ``/v1/chat/completions`` wire
format:

  - OpenAI itself (api.openai.com)
  - vLLM with ``--served-model-name``
  - Ollama in OpenAI-compat mode (``OLLAMA_HOST/v1``)
  - LiteLLM proxy
  - Together / Anyscale / Groq / Fireworks / Mistral La Plateforme / etc.

Hand-rolled httpx (no ``openai`` SDK dependency) — matches the convention
already used by ``eval/judges/factory.py`` and ``server/retrieval/hyde.py``.
Keeps pyproject.toml lean and gives full control over streaming, timeouts,
and error translation.

**Trade-offs vs the Anthropic adapter** (see the capability matrix in
``plugins/web2rag/CLAUDE.md``):

  - **No native Citations API.** Citations gracefully degrade to an empty
    list; the orchestrator marks ``citation_mode: "unavailable"`` in the
    SSE ``done`` event. Text-marker citation parsing (``[1]``/``[2]``) is
    intentionally not implemented — operators who pick a non-Claude backend
    accept the trade-off explicitly.
  - **No DocumentBlockParam.** Retrieved chunks are flattened into the
    system prompt as a ``<context>...</context>`` block via
    ``_helpers.documents_to_text_block``.
  - **No adaptive thinking.** OpenAI o1/o3 has a different param shape
    (``reasoning_effort``) — supporting it is future work, not in 0.5.0
    scope. The ``enable_adaptive_thinking`` port flag is silently ignored.
  - **No explicit prompt cache_control marker.** OpenAI 4o caches input
    automatically (no API surface to control it); vLLM/Ollama/Together
    don't cache at all. The ``cache_system_prompt`` flag is a no-op here.

**Exception translation.** Every httpx + HTTP error gets mapped to the
domain hierarchy in ``server/llm/errors.py`` so the orchestrator and
consumers don't need to know which adapter is active.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, AsyncIterator

import httpx

from server.config import settings
from server.llm.adapters._helpers import documents_to_text_block
from server.llm.errors import (
    LLMAuthError,
    LLMConnectionError,
    LLMStatusError,
    LLMTransientError,
)
from server.llm.ports import (
    ChatDoneEvent,
    ChatEvent,
    ChatTokenEvent,
    LLMCapabilities,
    LLMUsage,
)

log = logging.getLogger(__name__)


# ─── module-level client singletons ──────────────────────────────────────────
# One AsyncClient per adapter context. Lazy via lru_cache so the
# missing-config case is caught at request time, not at module import.
# httpx.AsyncClient is safe to reuse across concurrent requests — its
# connection pool is async-aware.


@lru_cache(maxsize=1)
def _get_chat_client() -> httpx.AsyncClient:
    # Long-deadline timeout — chat streams can run for minutes. The httpx
    # default (5s) would kill any meaningfully long answer. Per-chunk read
    # timeout is the real safety; total deadline at 10 min mirrors the
    # Anthropic adapter's behavior.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0),
    )


@lru_cache(maxsize=1)
def _get_rewrite_client() -> httpx.AsyncClient:
    # Rewrite is short — 60s is plenty.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
    )


# ─── capabilities ────────────────────────────────────────────────────────────


_CHAT_CAPABILITIES = LLMCapabilities(
    supports_streaming=True,
    supports_native_citations=False,
    supports_document_blocks=False,
    supports_adaptive_thinking=False,
    # OpenAI 4o caches automatically, no marker to set. Other backends
    # don't cache. The honest answer for the port abstraction is "no
    # explicit control surface."
    supports_prompt_cache_control=False,
)


_REWRITE_CAPABILITIES = LLMCapabilities(
    supports_streaming=False,
    supports_native_citations=False,
    supports_document_blocks=False,
    supports_adaptive_thinking=False,
    supports_prompt_cache_control=False,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _require_config() -> tuple[str, str]:
    """Resolve the base URL + chat model from settings. Raises LLMStatusError
    with an actionable message if either is missing."""
    base_url = (settings.openai_base_url or "").rstrip("/")
    if not base_url:
        raise LLMStatusError(
            "OPENAI_BASE_URL is empty — set it to your provider's /v1 endpoint "
            "(e.g. https://api.openai.com/v1 for OpenAI, http://vllm:8000/v1 "
            "for a self-hosted vLLM server, or http://ollama:11434/v1 for "
            "Ollama in OpenAI-compat mode)."
        )
    return base_url, settings.openai_chat_model


def _build_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    # Optional auth — self-hosted vLLM / Ollama typically run without a key.
    if settings.openai_api_key:
        headers["Authorization"] = f"Bearer {settings.openai_api_key}"
    return headers


def _translate_http_error(status_code: int, body_text: str, cause: Exception | None = None) -> Exception:
    """Map an HTTP status code to the right domain exception type.

    Body text is included in the message because OpenAI-compat backends
    differ a lot in how informative their errors are — preserving the
    original body in logs is the only way to debug some failures.
    """
    snippet = body_text[:500] if body_text else ""
    if status_code == 401:
        return LLMAuthError(
            f"authentication failed (401) — check OPENAI_API_KEY",
            status_code=401,
            cause=cause,
        )
    if status_code == 403:
        return LLMAuthError(
            f"permission denied (403) — API key may lack access to the model. body={snippet!r}",
            status_code=403,
            cause=cause,
        )
    if status_code in (429, 529):
        return LLMTransientError(
            f"transient backend error ({status_code}) — rate limit or overload. body={snippet!r}",
            cause=cause,
        )
    if 500 <= status_code < 600:
        return LLMStatusError(
            f"server error ({status_code}). body={snippet!r}",
            status_code=status_code,
            cause=cause,
        )
    return LLMStatusError(
        f"unexpected status {status_code}. body={snippet!r}",
        status_code=status_code,
        cause=cause,
    )


def _translate_httpx_error(exc: Exception) -> Exception:
    """Map httpx transport-level errors to domain types. Status-code-bearing
    HTTPStatusErrors are handled at the call site (we get the response body
    in hand there); this function handles the no-response failure surface."""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return LLMConnectionError(
            f"transport error reaching LLM backend: {type(exc).__name__}: {exc}",
            cause=exc,
        )
    if isinstance(exc, httpx.TransportError):
        return LLMConnectionError(
            f"transport error: {type(exc).__name__}: {exc}",
            cause=exc,
        )
    return LLMStatusError(
        f"unexpected error talking to LLM backend: {type(exc).__name__}: {exc}",
        cause=exc,
    )


def _build_system_with_context(system_prompt: str, documents: list[dict]) -> str:
    """Inject retrieved chunks into the system prompt as a <context> block.

    OpenAI / vLLM / Ollama have no native document primitive. The convention
    here mirrors what the literature recommends for retrieval-grounded
    prompting: place context above the question, mark it explicitly so the
    model knows what to anchor answers in, and instruct the model not to
    hallucinate URLs/IDs from the context (the base system prompt already
    does this for the Anthropic path; we preserve that instruction here).
    """
    context_block = documents_to_text_block(documents)
    if not context_block:
        return system_prompt
    return (
        f"{system_prompt}\n\n"
        f"You will be given retrieved context below. Answer ONLY using the "
        f"context. If the context doesn't contain enough to answer, say so "
        f"plainly. Cite sources by referencing the [N] marker that precedes "
        f"each document.\n\n"
        f"{context_block}"
    )


# ─── chat adapter ────────────────────────────────────────────────────────────


class OpenAICompatChatAdapter:
    """Streams chat completions through OpenAI's /v1/chat/completions SSE.

    Documents flattened into the system prompt; citations degrade to empty
    list with ``citation_mode: "unavailable"`` set by the orchestrator.
    """

    @property
    def capabilities(self) -> LLMCapabilities:
        return _CHAT_CAPABILITIES

    async def stream_chat(
        self,
        *,
        system_prompt: str,
        cache_system_prompt: bool,  # ignored — no explicit cache control surface
        messages: list[dict],
        documents: list[dict],
        max_tokens: int,
        model: str,
        enable_adaptive_thinking: bool,  # ignored — different param shape on o1/o3
    ) -> AsyncIterator[ChatEvent]:
        base_url, default_model = _require_config()
        target_model = model or default_model
        if not target_model:
            raise LLMStatusError(
                "no chat model configured — set OPENAI_CHAT_MODEL or pass model= explicitly."
            )

        # Build the OpenAI-shape messages array. System prompt carries the
        # flattened context block; user/assistant turns pass through.
        full_system = _build_system_with_context(system_prompt, documents)
        sdk_messages: list[dict] = [{"role": "system", "content": full_system}]
        for m in messages:
            sdk_messages.append({"role": m.get("role", "user"), "content": m.get("content", "") or ""})

        body: dict[str, Any] = {
            "model": target_model,
            "messages": sdk_messages,
            "max_tokens": max_tokens,
            "stream": True,
            # stream_options.include_usage exposes prompt/completion token
            # counts on the final chunk. Not every backend honors it; the
            # ones that don't simply omit it (we default to 0).
            "stream_options": {"include_usage": True},
        }

        url = f"{base_url}/chat/completions"
        headers = _build_headers()
        client = _get_chat_client()

        prompt_tokens = 0
        completion_tokens = 0
        # Some backends surface a cache_read counter under a different name
        # (e.g. ``prompt_tokens_details.cached_tokens`` on OpenAI 4o). We
        # capture whatever's there into provider_extras so operators get
        # visibility, but the orchestrator no longer flattens it into the
        # public SSE wire (that's reserved for Anthropic cache counters).
        extras: dict[str, int] = {}

        try:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    body_text = (await resp.aread()).decode("utf-8", errors="replace")
                    raise _translate_http_error(resp.status_code, body_text)
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        log.warning("openai-compat: dropping malformed SSE line: %r", payload[:200])
                        continue
                    # Token deltas — content streams in choices[0].delta.content.
                    choices = evt.get("choices") or []
                    if choices:
                        delta = (choices[0] or {}).get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            yield ChatTokenEvent(text=text)
                    # Usage — typically only on the final chunk when
                    # stream_options.include_usage is set.
                    usage = evt.get("usage")
                    if usage:
                        prompt_tokens = int(usage.get("prompt_tokens") or 0)
                        completion_tokens = int(usage.get("completion_tokens") or 0)
                        details = usage.get("prompt_tokens_details") or {}
                        cached = details.get("cached_tokens")
                        if cached is not None:
                            extras["prompt_cache_tokens"] = int(cached)
        except httpx.HTTPError as e:
            raise _translate_httpx_error(e) from e

        yield ChatDoneEvent(
            usage=LLMUsage(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                provider_extras=extras,
            )
        )


# ─── rewrite adapter ─────────────────────────────────────────────────────────


class OpenAICompatRewriteAdapter:
    """Non-streaming completion via /v1/chat/completions (stream=false)."""

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
        base_url, default_model = _require_config()
        target_model = model or settings.openai_rewrite_model or default_model
        if not target_model:
            raise LLMStatusError(
                "no rewrite model configured — set OPENAI_REWRITE_MODEL or OPENAI_CHAT_MODEL."
            )

        body = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }

        url = f"{base_url}/chat/completions"
        headers = _build_headers()
        client = _get_rewrite_client()

        try:
            r = await client.post(url, json=body, headers=headers)
            if r.status_code >= 400:
                raise _translate_http_error(r.status_code, r.text)
        except httpx.HTTPError as e:
            raise _translate_httpx_error(e) from e

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise LLMStatusError(
                f"openai-compat rewrite returned non-JSON body: {r.text[:200]!r}",
                cause=e,
            ) from e

        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = (choices[0] or {}).get("message") or {}
        return (msg.get("content") or "").strip()


__all__ = ["OpenAICompatChatAdapter", "OpenAICompatRewriteAdapter"]
