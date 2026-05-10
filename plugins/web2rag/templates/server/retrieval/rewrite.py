"""Query rewriting — multi-turn decontextualization.

If the user's message references prior turns ("how about for v2?"), we ask
Claude Haiku to rewrite it as a self-contained query before retrieval. This
costs one quick LLM call per turn but recovers ~60% of multi-turn failures
in published RAG benchmarks.

HyDE could plug in here too in a follow-up; we keep the surface stable.
"""
from __future__ import annotations

from anthropic import AsyncAnthropic

from server.config import settings


_REWRITE_SYSTEM = (
    "You rewrite the user's last message into a self-contained search query that does not "
    "depend on previous turns. Resolve pronouns, fill in implicit subjects, and merge "
    "follow-ups into one query. Mirror the language of the original message (English or "
    "Bahasa Indonesia). Output ONLY the rewritten query — no preamble, no explanation."
)


async def decontextualize(message: str, history: list[dict]) -> str:
    """Return a self-contained version of `message` given the conversation `history`.

    history: [{"role": "user"|"assistant", "content": str}, ...]
    """
    if not history:
        return message  # no prior turns → nothing to decontextualise

    convo = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history[-6:])
    user = f"Previous turns:\n{convo}\n\nLast message: {message}\n\nRewrite the last message as a self-contained query."

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.chat_model,
        max_tokens=settings.rewrite_max_tokens,
        system=_REWRITE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    return text or message
