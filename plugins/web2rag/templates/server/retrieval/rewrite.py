"""Query rewriting — multi-turn decontextualization.

If the user's message references prior turns ("how about for v2?"), we ask
the rewrite LLM to rewrite it as a self-contained query before retrieval.
Cheap (one quick LLM call per turn) but recovers ~60% of multi-turn failures
in published RAG benchmarks.

Since 0.4.0 the LLM call goes through ``server.llm.factory.rewrite_port()``
so the operator can swap providers via ``LLM_PROVIDER``. Today only Anthropic
is supported; 0.5.0 adds OpenAI-compat. The fallback model is ``chat_model``
when ``rewrite_model`` is empty — letting operators pick a cheap fast model
for rewrite even when chat uses something heavier.

HyDE could plug in here too in a follow-up; the surface stays stable.
"""
from __future__ import annotations

from server.config import settings
from server.llm.factory import rewrite_port


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
    user = (
        f"Previous turns:\n{convo}\n\nLast message: {message}\n\n"
        "Rewrite the last message as a self-contained query."
    )

    text = await rewrite_port().complete(
        system=_REWRITE_SYSTEM,
        user=user,
        max_tokens=settings.rewrite_max_tokens,
        # rewrite_model="" (default) → fall back to chat_model. Lets operators
        # pin a cheap Haiku here even when chat uses Sonnet/Opus.
        model=settings.rewrite_model or settings.chat_model,
    )
    return text or message
