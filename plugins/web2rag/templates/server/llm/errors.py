"""Domain-level exception hierarchy for the LLM port.

Adapters translate provider-specific exceptions (``anthropic.RateLimitError``,
``httpx.ConnectError``, raw HTTP 429s, etc.) into one of these types. The
orchestrator and consumers (``server/api/chat.py``, ``server/api/audit.py``)
catch domain types, not provider types, so the same exception ladder works
across every adapter.

This file landed in 0.5.0 when the second adapter (``OpenAICompatChatAdapter``)
forced an honest abstraction. The 0.4.0 release shipped without it — the
single Anthropic adapter let ``chat.py`` catch ``anthropic.*`` types directly,
which is the right design for a single-example abstraction. With two
adapters, the hierarchy below is what makes the port actually portable.

The split mirrors what an operator-facing error policy needs:

  - **LLMTransientError** — retry sensible. Rate limits, 5xx overload, 529s.
    Surfaced to the widget as "transient" — user sees "try again in a moment".
  - **LLMConnectionError** — network-side failure. DNS, TCP refused, mid-stream
    drop, read timeout. Surfaced as "connection" — user sees "could not reach
    the service".
  - **LLMAuthError** — operator-fixable. 401 (bad/expired key), 403 (key lacks
    permission for the model, e.g. ran out of credits, account in arrears).
    Distinct from generic 4xx because the operator action is different
    ("rotate API key" vs "look at request shape"). Mapped to a separate SSE
    error code so the widget can show a config-error message instead of a
    user-actionable "try again later".
  - **LLMStatusError** — everything else (other 4xx, unexpected 5xx). Carries
    the original status code for log triage.
  - **LLMError** — the common base. Catch this when you want "anything went
    wrong on the LLM side"; catch a specific subclass when you want to act
    on it.

Status codes preserved on subclasses that carry them (LLMStatusError +
LLMAuthError) so log handlers can still triage by code. The base
``LLMError`` carries the original exception in ``cause`` for forensic
inspection without going through ``__cause__`` chains.
"""
from __future__ import annotations


class LLMError(Exception):
    """Base for all port-level LLM errors. Catch this for any failure path."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class LLMTransientError(LLMError):
    """Retryable failure — rate limit, 529 overloaded, etc.

    Adapter should set this when the provider response indicates the
    operator can succeed by retrying without changing anything (waiting
    a moment, possibly with backoff).
    """


class LLMConnectionError(LLMError):
    """Network-side failure — DNS, TCP refused, read timeout, mid-stream drop.

    No HTTP response was received, OR the response was interrupted before
    completion. Retry MAY succeed; the operator should also check egress
    and DNS if this fires repeatedly.
    """


class LLMAuthError(LLMError):
    """Auth/permission failure — 401, 403, missing API key.

    Operator-fixable. Distinct from other 4xx because the response action
    differs (rotate key, check billing, grant model access). Mapped to a
    separate SSE error code so widgets can show a config-error message
    rather than a transient retry prompt.
    """

    def __init__(self, message: str, *, status_code: int | None = None, cause: Exception | None = None) -> None:
        super().__init__(message, cause=cause)
        self.status_code = status_code


class LLMStatusError(LLMError):
    """Generic non-retryable HTTP failure — 4xx that isn't 401/403/429, or
    unexpected 5xx that isn't 529.

    Carries ``status_code`` so the orchestrator / log handlers can triage
    without re-parsing the exception message.
    """

    def __init__(self, message: str, *, status_code: int | None = None, cause: Exception | None = None) -> None:
        super().__init__(message, cause=cause)
        self.status_code = status_code


__all__ = [
    "LLMError",
    "LLMTransientError",
    "LLMConnectionError",
    "LLMAuthError",
    "LLMStatusError",
]
