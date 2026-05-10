"""POST /retrieve — same retrieval pipeline as /chat, but stops BEFORE the LLM.

Cost-free preview path used by the `web2rag-chat-test` skill. The skill runs
this endpoint to grab the chunks the production /chat path would feed to
Claude, then hands them to a Claude Code subagent (in-session, $0 to the
operator) to produce a draft answer for inspection.

The endpoint runs:
  - guard_query                (real, Python — same as /chat)
  - hybrid_search + rerank     (real — same as /chat)
  - prompt.build_documents     (real — same as /chat)
  - prompt.SYSTEM_PROMPT       (returned verbatim — same as /chat)

It deliberately SKIPS:
  - rewrite (decontextualize) — that's a paid LLM call. v0.1 treats every
    test message as single-turn. Pass-through preserves zero cost.
  - the actual LLM call + guard_output. Those are simulated by prompt rules
    in the subagent skill.

Inviolable:
  - This endpoint stays $0. Adding a paid call here breaks the skill's
    cost guarantee and the plan's contract.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from server.config import settings
from server.guards.guard_query import guard_query
from server.retrieval.hyde import generate_hypothetical
from server.retrieval.prompt import SYSTEM_PROMPT, build_documents
from server.retrieval.rerank import rerank
from server.retrieval.search import hybrid_search
from server.store.acronyms import load as load_acronyms, load_all as load_all_acronyms
from server.util.lang import detect_language

router = APIRouter()
log = logging.getLogger(__name__)


class RetrievePayload(BaseModel):
    message: str = Field(..., min_length=1)
    site_id: str | None = None
    # Optional per-request overrides. None = use settings defaults. Bounded
    # to keep one careless caller from melting the box: top_k <= 100 covers
    # every realistic tuning sweep; rerank_top_n <= 25 covers every realistic
    # context-window budget. /chat and /audit are unaffected — they always
    # use settings defaults so production behaviour stays predictable.
    retrieve_top_k: int | None = Field(default=None, ge=1, le=100)
    rerank_top_n: int | None = Field(default=None, ge=1, le=25)


@router.post("/retrieve")
async def retrieve(payload: RetrievePayload) -> dict[str, Any]:
    top_k = payload.retrieve_top_k if payload.retrieve_top_k is not None else settings.retrieve_top_k
    top_n = payload.rerank_top_n if payload.rerank_top_n is not None else settings.rerank_top_n
    # rerank_top_n must be <= retrieve_top_k — clamp rather than 422 so callers
    # who pass top_k=5 don't have to also remember to lower top_n.
    top_n = min(top_n, top_k)

    # 1. guard_query — same Python middleware /chat runs.
    q_verdict = guard_query(payload.message, site_id=payload.site_id)
    if not q_verdict.passed:
        return {
            "site_id": payload.site_id,
            "language_detected": detect_language(payload.message),
            "guard_query": q_verdict.to_dict(),
            "blocked": True,
            "system_prompt": "",
            "documents": [],
            "chunks": [],
            "retrieve_top_k_used": top_k,
            "rerank_top_n_used": top_n,
        }

    message = q_verdict.redacted_text or payload.message

    # 2. HyDE — if enabled, ask the configured LLM to write a hypothetical
    #    answer to the query. The hypothetical bridges vocabulary gaps the
    #    auto-acronym table can't cover (synonyms, paraphrases). The LLM
    #    gets the corpus's auto-extracted acronym table as a glossary so
    #    domain-specific terms are grounded. Returns None when disabled or
    #    on any failure — retrieval proceeds without a hypothetical, which
    #    is identical to the pre-HyDE behaviour. Acronym table is loaded
    #    here once and threaded through both HyDE (for the glossary) and
    #    hybrid_search (which loads it again — cheap disk read).
    acronyms = (
        load_acronyms(payload.site_id) if payload.site_id else load_all_acronyms()
    )
    hypothetical = await generate_hypothetical(message, acronyms=acronyms)

    # 3. retrieval — identical to /chat's path, except for the per-request
    #    top-k/top-n overrides honored here AND the optional HyDE variant.
    where = {"site_id": payload.site_id} if payload.site_id else None
    candidates = hybrid_search(message, top_k=top_k, where=where, hypothetical=hypothetical)
    hits = rerank(message, candidates, top_n=top_n)

    # 3. shape what the subagent needs.
    documents = build_documents(hits)  # DocumentBlockParam shape (Anthropic Citations)
    # `chunks` is a flatter view the skill prints to the terminal.
    chunks = [
        {
            "index": i,
            "score": round(h.score, 4),
            "source_url": h.metadata.get("source_url", ""),
            "page_title": h.metadata.get("page_title", ""),
            "section_title": h.metadata.get("section_title", ""),
            "language": h.metadata.get("language", ""),
            "text": h.text,
        }
        for i, h in enumerate(hits)
    ]

    return {
        "site_id": payload.site_id,
        "language_detected": detect_language(payload.message),
        "guard_query": q_verdict.to_dict(),
        "blocked": False,
        "system_prompt": SYSTEM_PROMPT,
        "documents": documents,    # for the subagent prompt
        "chunks": chunks,          # for terminal display
        "retrieve_top_k_used": top_k,
        "rerank_top_n_used": top_n,
        # HyDE bookkeeping for the chat-test skill / debugging. `hyde_used`
        # is true only when HyDE was enabled AND the LLM call succeeded
        # AND produced non-empty output. `hyde_text` lets operators see
        # what the LLM hallucinated as the "ideal answer" — useful for
        # understanding why retrieval ranked the chunks it did.
        "hyde_used": hypothetical is not None,
        "hyde_text": hypothetical,
    }
