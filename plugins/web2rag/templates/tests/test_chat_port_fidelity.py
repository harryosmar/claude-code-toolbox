"""Port-fidelity test — locks the public SSE event shape of stream_chat().

This test was written to anchor the 0.4.0 port/adapter refactor: it captures
the EXACT sequence and shape of SSE events emitted by
``server.llm.claude.stream_chat`` against a mocked Anthropic SDK stream, then
asserts the same shape continues to be emitted after every refactor step.

If the refactor accidentally renames a field, drops a dedup step, reorders
events, or changes the ``done.usage`` flattening, this test fails loudly.
That is its only job — it does not exercise networking, retrieval, or guards.

Why a standalone runnable script, not pytest:

  - The web2rag generated project does not (yet) bundle a pytest runner; its
    pyproject.toml only ships runtime deps.
  - This test imports server modules directly and patches them in-process.
    It needs nothing pytest provides.
  - The file follows pytest's ``test_*.py`` naming convention so a future
    operator who adds pytest to their project picks it up for free — but it
    is also runnable as ``python -m tests.test_chat_port_fidelity`` from the
    project root.

Duck-typed mocks: ``stream_chat`` reads event attributes via
``getattr(event, "type", None)`` everywhere. We exploit that by passing in
``SimpleNamespace`` objects with just the attributes the code path actually
inspects. No dependency on Anthropic SDK class names.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


# Bootstrap path so `python tests/test_chat_port_fidelity.py` works from any cwd.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# Pin the provider to anthropic so this test isn't affected by env left over
# from a previous test run (the openai-compat test sets LLM_PROVIDER=openai-compat).
os.environ["LLM_PROVIDER"] = "anthropic"


# ─── canned Anthropic SDK event sequence ──────────────────────────────────────
# Reproduces what a successful 2-token, 2-citation response looks like coming
# out of ``client.messages.stream``. The duck-typed objects only carry the
# attributes ``stream_chat`` actually reads — anything else is ignored.

def _text_delta_event(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _citation_delta_event(
    *,
    document_index: int,
    cited_text: str,
    start: int | None,
    end: int | None,
    document_title: str = "",
) -> SimpleNamespace:
    citation = SimpleNamespace(
        type="char_location",
        cited_text=cited_text,
        document_index=document_index,
        document_title=document_title,
        start_char_index=start,
        end_char_index=end,
    )
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="citations_delta", citation=citation),
    )


# Final-message stub returned by ``stream.get_final_message()``. Carries the
# usage shape the orchestrator reads.
def _final_message() -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=18,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=512,
        ),
    )


# ─── recorded event sequence ──────────────────────────────────────────────────
# Intentionally exercises the dedup path: two citations with identical
# (document_index, start, end) — _hydrate must keep only one. Also includes
# an out-of-range document_index (99) — _hydrate must drop it.

CANNED_EVENTS: list[SimpleNamespace] = [
    _text_delta_event("Hello "),
    _citation_delta_event(document_index=0, cited_text="alpha", start=10, end=15),
    _text_delta_event("world."),
    # Duplicate of the previous citation — must be deduped.
    _citation_delta_event(document_index=0, cited_text="alpha", start=10, end=15),
    # Out-of-range index — must be dropped (hits[] has only 2 entries below).
    _citation_delta_event(document_index=99, cited_text="ghost", start=0, end=5),
    # Distinct citation — must survive.
    _citation_delta_event(document_index=1, cited_text="beta", start=20, end=25),
]


# ─── mock stream context manager ──────────────────────────────────────────────
@dataclass
class _MockStream:
    events: list[SimpleNamespace]
    final: SimpleNamespace

    def __aiter__(self):
        async def _gen():
            for e in self.events:
                yield e
        return _gen()

    async def get_final_message(self):
        return self.final


@asynccontextmanager
async def _mock_stream_cm(*args, **kwargs):
    yield _MockStream(events=CANNED_EVENTS, final=_final_message())


# ─── expected SSE shape (the contract this test locks in) ─────────────────────
# Two text tokens, then a citations event with deduped + filtered list, then
# a done event with usage + answer_chars + model.

EXPECTED_EVENTS: list[dict[str, Any]] = [
    {"type": "token", "text": "Hello "},
    {"type": "token", "text": "world."},
    {
        "type": "citations",
        "citations": [
            {
                "index": 0,
                "url": "https://example.test/doc-a",
                "title": "Doc A",
                "section_title": "Intro",
                "snippet": "alpha",
                "char_start": 10,
                "char_end": 15,
            },
            {
                "index": 1,
                "url": "https://example.test/doc-b",
                "title": "Doc B",
                "section_title": "Body",
                "snippet": "beta",
                "char_start": 20,
                "char_end": 25,
            },
        ],
    },
    {
        "type": "done",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 18,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 512,
        },
        "answer_chars": len("Hello ") + len("world."),
        "model": "claude-haiku-4-5-20251001",  # default chat_model
        # Added in 0.5.0 — Anthropic adapter declares native citation
        # support, so the orchestrator emits "native" here. OpenAI-compat
        # path emits "unavailable" (covered in test_openai_compat_port_fidelity.py).
        "citation_mode": "native",
    },
]


# ─── the actual test ──────────────────────────────────────────────────────────
async def _run_stream_chat_collected() -> list[dict[str, Any]]:
    """Invoke stream_chat with the mocked Anthropic client; return all yields."""
    # Import inside the function so the path-bootstrap above takes effect.
    from server.llm import claude  # noqa: WPS433
    from server.retrieval.search import Hit

    hits = [
        Hit(
            chunk_id="c0",
            text="...",
            metadata={
                "source_url": "https://example.test/doc-a",
                "page_title": "Doc A",
                "section_title": "Intro",
            },
            score=0.9,
        ),
        Hit(
            chunk_id="c1",
            text="...",
            metadata={
                "source_url": "https://example.test/doc-b",
                "page_title": "Doc B",
                "section_title": "Body",
            },
            score=0.8,
        ),
    ]

    # Rebuild settings so the LLM_PROVIDER env we set at module-top takes
    # effect (Settings reads env at construction time). Clear factory
    # lru_caches so any cached adapter from a previous run is dropped.
    from server.config import Settings  # noqa: WPS433
    import server.config as _config_mod  # noqa: WPS433
    _config_mod.settings = Settings()
    from server.llm import factory  # noqa: WPS433
    factory.chat_port.cache_clear()
    factory.rewrite_port.cache_clear()

    # Patch the Anthropic client at the adapter boundary so any
    # ``_get_chat_client()`` call inside ``AnthropicChatAdapter.stream_chat``
    # returns our stub. Test couples to the adapter module location (the
    # natural place to mock per-provider behavior); the orchestrator
    # (``claude.py``) stays provider-agnostic and untouched.
    from server.llm.adapters import anthropic_adapter  # noqa: WPS433

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(stream=_mock_stream_cm),
    )
    # Clear the lru_cache so a previously-constructed real client doesn't
    # interfere with the patch.
    anthropic_adapter._get_chat_client.cache_clear()
    with patch.object(anthropic_adapter, "_get_chat_client", lambda: fake_client):
        out: list[dict[str, Any]] = []
        async for evt in claude.stream_chat(
            user_message="What is alpha?",
            history=[],
            documents=[
                {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "..."}},
                {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "..."}},
            ],
            system_prompt="You are a helpful assistant.",
            hits=hits,
        ):
            out.append(evt)
        return out


def _assert_shape(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    """Walk both lists and assert key/value equality with helpful error context."""
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


def test_stream_chat_port_fidelity() -> None:
    """Pytest-discoverable entrypoint — also works as plain function."""
    actual = asyncio.run(_run_stream_chat_collected())
    _assert_shape(actual, EXPECTED_EVENTS)


if __name__ == "__main__":
    try:
        test_stream_chat_port_fidelity()
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
    print("OK — port-fidelity baseline matches.")
