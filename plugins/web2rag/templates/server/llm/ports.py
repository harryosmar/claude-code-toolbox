"""LLM port interfaces — the seam between chat orchestration and provider SDKs.

This module defines two ports (Protocols) for the LLM-facing parts of the api:

  - **LLMChatPort**     — streaming chat with documents + citations (the /chat path)
  - **LLMRewritePort**  — non-streaming text completion (the multi-turn rewrite path)

Two ports rather than one because rewrite is non-streaming, has no documents,
and emits no citations. Forcing a single port means every adapter implements
a degenerate stream_chat for the rewrite use case. Two narrow ports = each
adapter implements only what it actually does.

Capability matrix (canonical reference — also surfaces on GET /health):

    | Capability                | Anthropic | OpenAI-compat (0.5.0) |
    |---------------------------|-----------|------------------------|
    | Native streaming          |     ✓     |          ✓             |
    | Native Citations API      |     ✓     |          ✗             |
    | Document blocks           |     ✓     |  flattened to system   |
    | Adaptive thinking         |     ✓     |          ✗             |
    | Prompt cache_control      |     ✓     |  auto on OpenAI 4o     |

Non-Anthropic adapters declare ``supports_native_citations=False`` and the
orchestrator emits an empty citations list. Text-marker citation parsing
(``[1]`` / ``[2]`` markers) is intentionally NOT implemented — it's fragile
across models. Operators who choose a non-Claude backend explicitly accept
the trade-off.

Design choices anchored in the validation:

  - **Documents canonical input shape = Anthropic DocumentBlockParam dict.**
    This is the LEAD provider's native shape. Non-Anthropic adapters flatten
    it via a shared helper in 0.5.0. This is honest design — the shape
    happens to match Anthropic because Anthropic is the lead adapter — not
    a leaky abstraction.
  - **`cache_system_prompt` is an explicit orchestrator input, not implicit
    inside the adapter.** Orchestrator owns the intent ("cache the system
    prompt"); adapter owns the mechanism (Anthropic wraps in cache_control;
    OpenAICompat no-ops since OpenAI caches automatically and others don't
    support markers).
  - **`enable_adaptive_thinking` is passed through; adapter silently no-ops
    when the model doesn't support it.** Operators set the flag once and
    every adapter does the right thing. The model-capability registry lives
    inside the adapter, not the orchestrator.
  - **`LLMUsage.provider_extras` is an extras bag**, not a strict dataclass.
    Anthropic's cache_creation_input_tokens / cache_read_input_tokens carry
    operational meaning (zero from Anthropic = silent invalidator; zero from
    OpenAI = no cache concept). Forcing every adapter to populate zeros would
    pollute observability.
  - **`PortCitation.start_char_index` / `end_char_index` are Optional** —
    Anthropic populates them; OpenAICompat (0.5.0) sets None.
  - **`capabilities` is exposed via `@property` on the Protocol, not as a
    class attribute** — keeps runtime_checkable semantics clean.

Not in scope here:

  - **server/guards/prompt_guard.py** has its own Protocol-based local /
    anthropic adapter seam. It's intentionally separate because it uses a
    SYNC Anthropic client (FastAPI worker-level concurrency, not async — see
    prompt_guard.py header). Folding it through this port would force-marry
    different concurrency contracts.
  - **eval/judges/factory.py** is already multi-provider (anthropic-haiku /
    ollama / openai-compat / custom-openai). Audit has different needs from
    chat (Batch API, cost rollup) and is out of scope.
  - **server/retrieval/hyde.py** has its own 60-line OpenAI-compat client.
    See hyde.py header — HyDE has determinism requirements that differ from
    rewrite (HyDE is /retrieve-only, excluded from /chat and /audit to
    preserve reproducibility). Unifying would couple retrieval-time
    determinism to rewrite-port evolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol, runtime_checkable


# ─── capability dataclass ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMCapabilities:
    """What an adapter says it can do.

    Surfaced on GET /health so operators can verify what they're running
    without reading source. Used by the orchestrator NOT for gating (the
    adapter no-ops silently when a flag doesn't apply) but for diagnostics.
    """

    supports_streaming: bool
    supports_native_citations: bool
    supports_document_blocks: bool
    supports_adaptive_thinking: bool
    supports_prompt_cache_control: bool


# ─── port event types (the wire between adapter and orchestrator) ────────────


@dataclass(frozen=True)
class PortCitation:
    """One citation, in port-canonical shape.

    The orchestrator hydrates ``document_index`` against its ``hits[]`` list
    to fill in URL / title / section_title — those don't live on the
    citation itself because they're per-Hit metadata, not per-citation.

    ``start_char_index`` / ``end_char_index`` are Optional because some
    adapters (OpenAI / Ollama / etc.) don't emit char-level offsets.
    """

    document_index: int
    cited_text: str
    start_char_index: int | None
    end_char_index: int | None


@dataclass(frozen=True)
class LLMUsage:
    """Token usage for one completion. The two universal fields are always
    populated; provider-specific counters land in ``provider_extras``.

    Anthropic populates ``cache_creation_input_tokens`` and
    ``cache_read_input_tokens`` in extras. OpenAI-compat (0.5.0) will
    populate ``prompt_cache_tokens`` when the response surfaces it.

    The orchestrator flattens ``provider_extras`` into the public SSE
    ``done.usage`` dict so the wire format keeps the same flat shape today.
    """

    input_tokens: int
    output_tokens: int
    provider_extras: dict[str, int] = field(default_factory=dict)


# ─── chat event union (yielded by LLMChatPort.stream_chat) ───────────────────


@dataclass(frozen=True)
class ChatTokenEvent:
    """One streaming text fragment. Emitted as it arrives."""

    text: str
    type: Literal["token"] = "token"


@dataclass(frozen=True)
class ChatCitationEvent:
    """One raw citation. Orchestrator buffers and dedups/hydrates after the stream."""

    citation: PortCitation
    type: Literal["citation"] = "citation"


@dataclass(frozen=True)
class ChatDoneEvent:
    """Final event with usage. No further events follow."""

    usage: LLMUsage
    type: Literal["done"] = "done"


ChatEvent = ChatTokenEvent | ChatCitationEvent | ChatDoneEvent


# ─── the ports ────────────────────────────────────────────────────────────────


@runtime_checkable
class LLMChatPort(Protocol):
    """The streaming chat port — used by /chat and /audit.

    Conventions:

      - ``messages``: full conversation including the current user turn,
        with string content only. Document blocks are NOT in here — they're
        passed separately as ``documents`` so non-Anthropic adapters can
        flatten them into the system prompt instead of splicing them into
        the user message content array.
      - ``documents``: list of dicts in Anthropic DocumentBlockParam shape
        (the canonical port input). The Anthropic adapter passes them
        through as-is; other adapters flatten them.
      - ``cache_system_prompt``: orchestrator's explicit intent — adapter
        decides the mechanism. Anthropic wraps in cache_control; OpenAI
        no-ops (caching is automatic on 4o); others no-op.
      - ``enable_adaptive_thinking``: orchestrator passes the operator's
        flag through; adapter silently no-ops on models that don't support
        it. Owners of the model-capability registry: the adapter, not the
        orchestrator.
    """

    @property
    def capabilities(self) -> LLMCapabilities: ...

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
    ) -> AsyncIterator[ChatEvent]: ...


@runtime_checkable
class LLMRewritePort(Protocol):
    """The query-rewrite port — used by multi-turn decontextualization.

    Non-streaming, no documents, no citations. The caller passes the system
    prompt + the user text, gets a string back. Used by
    ``server/retrieval/rewrite.py`` to rewrite ambiguous follow-up queries
    ("how about for v2?") into self-contained ones before retrieval.
    """

    @property
    def capabilities(self) -> LLMCapabilities: ...

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        model: str,
    ) -> str: ...


__all__ = [
    "LLMCapabilities",
    "PortCitation",
    "LLMUsage",
    "ChatTokenEvent",
    "ChatCitationEvent",
    "ChatDoneEvent",
    "ChatEvent",
    "LLMChatPort",
    "LLMRewritePort",
]
