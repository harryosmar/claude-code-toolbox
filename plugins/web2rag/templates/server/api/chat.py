"""POST /chat — the widget's entrypoint.

Pipeline:
  user_msg
    → guard_query   (free, in-process)
    → rewrite       (multi-turn decontextualization, paid)
    → hybrid_search (BM25 + dense, free + RRF fused)
    → rerank        (cross-encoder, free, in-process)
    → Claude        (native Citations API, paid; streams tokens live)
    → guard_output  (post-stream; emits a `guard_amend` event if it rewrote)
    → SSE flush

The widget receives token deltas LIVE so the UX stays snappy. After the
stream ends, guard_output may emit a single `guard_amend` event with a
corrected answer — the widget swaps it in with a "(corrected)" badge.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from server.config import settings
from server.guards.guard_output import filter_output
from server.guards.guard_query import guard_query
from server.llm.claude import stream_chat
from server.retrieval.prompt import build_documents, build_system_prompt
from server.retrieval.rerank import rerank
from server.retrieval.rewrite import decontextualize
from server.retrieval.search import hybrid_search

router = APIRouter()
log = logging.getLogger(__name__)


class ChatPayload(BaseModel):
    message: str = Field(..., min_length=1)
    site_id: str | None = None
    session_id: str | None = None
    history: list[dict] = Field(default_factory=list)


@router.post("/chat")
async def chat(payload: ChatPayload) -> EventSourceResponse:
    if not settings.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured. Run /web2rag-setup.")

    async def stream() -> AsyncIterator[dict]:
        # ── guard_query ──────────────────────────────────────────────────────
        q_verdict = guard_query(payload.message, site_id=payload.site_id)
        if not q_verdict.passed:
            yield {"event": "blocked", "data": json.dumps({"type": "blocked", "guard": "query", **q_verdict.to_dict()})}
            return
        message = q_verdict.redacted_text or payload.message

        # ── rewrite (multi-turn decontextualization) ────────────────────────
        if payload.history:
            try:
                message = await decontextualize(message, payload.history)
            except Exception:  # noqa: BLE001
                log.exception("rewrite failed; falling back to original message")

        # ── retrieve + rerank ───────────────────────────────────────────────
        where = {"site_id": payload.site_id} if payload.site_id else None
        candidates = hybrid_search(message, top_k=settings.retrieve_top_k, where=where)
        hits = rerank(message, candidates, top_n=settings.rerank_top_n)
        if not hits:
            yield {"event": "no_context", "data": json.dumps({"type": "no_context"})}
            yield {"event": "done", "data": json.dumps({"type": "done", "answer_chars": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "guards": {"query": q_verdict.to_dict()}})}
            return

        documents = build_documents(hits)

        # ── stream from Claude (live tokens) ────────────────────────────────
        # Anthropic errors surface mid-stream — emit a structured SSE error
        # event so the widget can render a retry affordance instead of
        # silently dropping the connection. Typed exception hierarchy:
        # transient (rate_limit / overloaded) is retryable, connection is
        # network-side, APIStatusError is the everything-else catchall.
        buffered_text = ""
        raw_citations: list[dict] = []
        usage: dict = {}
        try:
            async for evt in stream_chat(
                user_message=message,
                history=payload.history,
                documents=documents,
                system_prompt=build_system_prompt(payload.site_id),
                hits=hits,
            ):
                if evt["type"] == "token":
                    buffered_text += evt["text"]
                    yield {"event": "token", "data": json.dumps(evt)}
                elif evt["type"] == "citations":
                    raw_citations = evt.get("citations", [])
                elif evt["type"] == "done":
                    usage = evt.get("usage", {})
        except anthropic.RateLimitError as e:
            log.warning("anthropic rate limit during /chat: %s", e)
            yield {"event": "error", "data": json.dumps({
                "type": "error",
                "code": "transient",
                "message": "The chatbot is temporarily busy. Please try again in a moment.",
            })}
            return
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            log.warning("anthropic connection error during /chat: %s", e)
            yield {"event": "error", "data": json.dumps({
                "type": "error",
                "code": "connection",
                "message": "Could not reach the chatbot service. Please try again.",
            })}
            return
        except anthropic.APIStatusError as e:
            # 529 (Overloaded) is functionally transient — the pinned SDK
            # (<0.50) does not expose a dedicated OverloadedError class,
            # so we branch on status_code instead.
            if e.status_code == 529:
                log.warning("anthropic overloaded during /chat: %s", e)
                code, message = "transient", "The chatbot is temporarily busy. Please try again in a moment."
            else:
                log.exception("anthropic api error during /chat (status=%s)", e.status_code)
                code, message = "api", "The chatbot service returned an error. Please try again later."
            yield {"event": "error", "data": json.dumps({"type": "error", "code": code, "message": message})}
            return

        # ── guard_output (post-stream) ──────────────────────────────────────
        out_verdict, final_answer, final_citations = filter_output(
            answer=buffered_text,
            citations=raw_citations,
            hits=hits,
        )

        # If guard_output rewrote the answer materially, tell the widget to swap.
        if final_answer != buffered_text:
            yield {"event": "guard_amend", "data": json.dumps({"type": "guard_amend", "text": final_answer, "reasons": out_verdict.reasons})}

        yield {"event": "citations", "data": json.dumps({"type": "citations", "citations": final_citations})}
        yield {"event": "done", "data": json.dumps({
            "type": "done",
            "usage": usage,
            "guards": {"query": q_verdict.to_dict(), "output": out_verdict.to_dict()},
        })}

    return EventSourceResponse(stream())
