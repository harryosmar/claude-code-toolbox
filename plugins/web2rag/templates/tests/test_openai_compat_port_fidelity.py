"""Port-fidelity test for the openai-compat adapter — locks the SSE shape
that the orchestrator emits when LLM_PROVIDER=openai-compat.

Companion to ``tests/test_chat_port_fidelity.py`` (which covers the
Anthropic path). The difference matters because the wire shape differs:

  - citation_mode: "unavailable" (not "native")
  - citations list: empty (no native Citations API)
  - usage.cache_creation_input_tokens / cache_read_input_tokens: 0
    (Anthropic-specific counters; OpenAI-compat populates
    ``prompt_cache_tokens`` in provider_extras instead — not surfaced
    on the wire today, only via the adapter capability declaration)

Approach: same duck-typed mocking as the Anthropic test, but at the
httpx layer (the adapter speaks raw httpx, not an SDK). We patch
``server.llm.adapters.openai_compat_adapter._get_chat_client`` to return
a fake AsyncClient whose ``.stream`` yields a recorded OpenAI SSE event
sequence.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


# Bootstrap path so this runs from any cwd.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# Force settings to point at openai-compat for this test. We do this BEFORE
# importing the factory (which lru_caches the resolved adapter).
os.environ["LLM_PROVIDER"] = "openai-compat"
os.environ["OPENAI_BASE_URL"] = "https://api.example.test/v1"
os.environ["OPENAI_CHAT_MODEL"] = "test-model"
os.environ["OPENAI_API_KEY"] = "test-key"


# ─── canned OpenAI SSE event sequence ────────────────────────────────────────
# Two content deltas (Hello / world.), one final chunk carrying usage.

_SSE_EVENTS: list[str] = [
    'data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"}}]}',
    'data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello "}}]}',
    'data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"world."}}]}',
    'data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,"finish_reason":"stop","delta":{}}],'
        '"usage":{"prompt_tokens":120,"completion_tokens":18,"prompt_tokens_details":{"cached_tokens":64}}}',
    'data: [DONE]',
]


class _FakeStreamResponse:
    """Duck-typed httpx Response for the streaming path."""

    status_code = 200

    async def aiter_lines(self):
        for line in _SSE_EVENTS:
            yield line

    async def aread(self) -> bytes:
        return b""


@asynccontextmanager
async def _fake_stream(*args, **kwargs):
    yield _FakeStreamResponse()


# Fake httpx AsyncClient — only the .stream method is exercised here.
def _fake_client() -> SimpleNamespace:
    return SimpleNamespace(stream=_fake_stream)


# ─── expected SSE shape (the contract this test locks in) ────────────────────
# Two tokens, an EMPTY citations list (adapter says supports_native_citations=False),
# a done event with citation_mode="unavailable" and zero cache counters.

EXPECTED_EVENTS: list[dict[str, Any]] = [
    {"type": "token", "text": "Hello "},
    {"type": "token", "text": "world."},
    {
        "type": "citations",
        "citations": [],  # no citations on non-Anthropic adapters
    },
    {
        "type": "done",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 18,
            # Anthropic-specific cache counters default to 0 on openai-compat.
            # OpenAI's prompt_cache_tokens lives in provider_extras (not
            # surfaced here in 0.5.0).
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "answer_chars": len("Hello ") + len("world."),
        "model": "claude-haiku-4-5-20251001",  # still the default chat_model; the
                                                # done event reports settings.chat_model
                                                # which is the Claude default; openai-compat
                                                # operators set OPENAI_CHAT_MODEL separately.
                                                # See claude.py / .env.example.tmpl for context.
        "citation_mode": "unavailable",
    },
]


# ─── the test ───────────────────────────────────────────────────────────────


async def _run_stream_chat_collected() -> list[dict[str, Any]]:
    # Re-import settings AFTER env is set so pydantic-settings picks up the
    # openai-compat config. The factory's lru_cache also needs clearing for
    # the same reason.
    from server.config import Settings  # noqa: WPS433
    import server.config as _config_mod  # noqa: WPS433
    _config_mod.settings = Settings()  # rebuild with the patched env

    from server.llm import factory  # noqa: WPS433
    factory.chat_port.cache_clear()
    factory.rewrite_port.cache_clear()

    from server.llm import claude  # noqa: WPS433
    from server.llm.adapters import openai_compat_adapter  # noqa: WPS433
    from server.retrieval.search import Hit

    hits = [
        Hit(
            chunk_id="c0",
            text="Alpha doc.",
            metadata={
                "source_url": "https://example.test/doc-a",
                "page_title": "Doc A",
                "section_title": "Intro",
            },
            score=0.9,
        ),
        Hit(
            chunk_id="c1",
            text="Beta doc.",
            metadata={
                "source_url": "https://example.test/doc-b",
                "page_title": "Doc B",
                "section_title": "Body",
            },
            score=0.8,
        ),
    ]

    openai_compat_adapter._get_chat_client.cache_clear()
    with patch.object(openai_compat_adapter, "_get_chat_client", _fake_client):
        out: list[dict[str, Any]] = []
        async for evt in claude.stream_chat(
            user_message="What is alpha?",
            history=[],
            documents=[
                {"type": "document", "source": {"type": "text", "data": "Alpha is a thing."}, "title": "Doc A", "context": "https://example.test/doc-a"},
                {"type": "document", "source": {"type": "text", "data": "Beta is another."}, "title": "Doc B", "context": "https://example.test/doc-b"},
            ],
            system_prompt="You are a helpful assistant.",
            hits=hits,
        ):
            out.append(evt)
        return out


def _assert_shape(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    if len(actual) != len(expected):
        raise AssertionError(
            f"event count mismatch: got {len(actual)}, expected {len(expected)}\n"
            f"actual: {actual}"
        )
    for i, (a, e) in enumerate(zip(actual, expected)):
        if a != e:
            raise AssertionError(
                f"event #{i} mismatch:\n  actual:   {a}\n  expected: {e}"
            )


def test_stream_chat_openai_compat_port_fidelity() -> None:
    actual = asyncio.run(_run_stream_chat_collected())
    _assert_shape(actual, EXPECTED_EVENTS)


if __name__ == "__main__":
    try:
        test_stream_chat_openai_compat_port_fidelity()
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
    print("OK — openai-compat port-fidelity baseline matches.")
