"""HyDE — Hypothetical Document Embeddings, async OpenAI-compat client.

Asks an LLM to write a hypothetical answer to the user's question, given the
corpus's auto-extracted acronym table as ground-truth domain vocabulary.
The hypothetical is embedded alongside the original query in `hybrid_search`
and RRF-fused — this bridges vocabulary gaps that don't fit the regex-based
acronym expansion (synonyms, paraphrases, definitional intent).

Why a self-contained client (instead of reusing eval/judges/factory.py):

  - The judge factory lives under `eval/` because it's audit-scoped. HyDE
    runs on the chat-test/retrieve path, which is `server/`. Importing
    `eval` from `server` reverses the existing dependency direction.
  - The judge factory also exposes batch APIs and Anthropic-specific helpers
    (`generate_batch`) that HyDE doesn't need. A 60-line OpenAI-compat
    client tailored to HyDE's single use case is clearer.
  - Keeps the audit pipeline's determinism guarantees clean: HyDE changes
    behaviour on /retrieve only, with no risk of leaking into /audit's
    judge wiring.

Graceful degradation: any failure (HYDE_ENABLED=false, missing config,
LLM unreachable, timeout, HTTP error, malformed JSON, empty content) is
logged and returns None. Callers treat None as "no hypothetical, run
retrieval with whatever variants we have already."
"""
from __future__ import annotations

import logging
from typing import Any

from server.config import settings

log = logging.getLogger(__name__)


_SYSTEM_PROMPT_TEMPLATE = """\
You are writing a brief hypothetical answer to a user's question, in the
SAME language as the question. The answer will be used as a retrieval
seed — it does not need to be factually correct, only plausible and
written in the vocabulary that real answers in this corpus would use.

Constraints:
  - 2-3 sentences only. No greetings, no caveats, no disclaimers.
  - Use the corpus's domain glossary verbatim where relevant (see below).
  - Mirror the user's question language exactly (English ↔ Bahasa
    Indonesia ↔ whatever language was asked).
  - Do NOT invent URLs, IDs, dates, or numbers.
  - If you don't know the answer, write the kind of sentence a real answer
    would have — the embedding will pull the right chunks regardless.

Corpus domain glossary (acronyms expanded from the corpus itself):
{glossary}
"""


def _build_glossary(acronyms: dict[str, str]) -> str:
    """Render the acronym table as a glossary block in the system prompt.
    Keeps the prompt token budget bounded — we cap at the 30 most "useful"
    entries when the table is large. For most deployments the table is
    well under 30 entries so this is a no-op."""
    if not acronyms:
        return "  (none)"
    items = sorted(acronyms.items())[:30]
    return "\n".join(f"  {acro} = {full}" for acro, full in items)


async def generate_hypothetical(
    query: str,
    *,
    acronyms: dict[str, str] | None = None,
) -> str | None:
    """Generate a hypothetical answer for `query` via the configured HyDE LLM.

    Returns the hypothetical text on success, or None on any failure path
    (disabled, misconfigured, unreachable, timeout, empty). The caller
    branches on `is None` to decide whether to add the hypothetical as a
    retrieval variant.

    The acronym table is woven into the system prompt as a glossary so the
    LLM uses corpus-grounded vocabulary instead of relying on training-data
    guesses for domain-specific terms. This is what makes HyDE work on
    corpora the LLM has never seen.
    """
    if not settings.hyde_enabled:
        return None
    if not settings.hyde_url or not settings.hyde_model:
        log.warning("HYDE_ENABLED=true but HYDE_URL or HYDE_MODEL is empty — skipping")
        return None
    if not query or not query.strip():
        return None

    glossary = _build_glossary(acronyms or {})
    system = _SYSTEM_PROMPT_TEMPLATE.format(glossary=glossary)

    body: dict[str, Any] = {
        "model": settings.hyde_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
        "max_tokens": settings.hyde_max_tokens,
        # Some randomness helps HyDE explore vocabulary — pure-greedy
        # decoding can lock into a single phrasing that misses corpus
        # variants. 0.3 is enough variation without going off-task.
        "temperature": 0.3,
        "stream": False,
    }

    headers = {"Content-Type": "application/json"}
    if settings.hyde_api_key:
        headers["Authorization"] = f"Bearer {settings.hyde_api_key}"

    base = settings.hyde_url.rstrip("/")
    url = f"{base}/chat/completions"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=settings.hyde_timeout_s) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001 — graceful degradation by design
        log.warning("HyDE call failed (%s): %s", type(e).__name__, e)
        return None

    choices = data.get("choices") or []
    if not choices:
        log.warning("HyDE returned no choices: %r", data)
        return None
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        return None

    log.info("HyDE generated %d chars for query=%r", len(content), query[:50])
    return content


__all__ = ["generate_hypothetical"]
