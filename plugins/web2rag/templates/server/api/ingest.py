"""POST /ingest — kicks off a crawl + chunk + embed + upsert pipeline.

Streams progress as Server-Sent Events. The /web2rag-ingest skill consumes
the stream and prints progress lines to the operator.

GET /sites and DELETE /sites/{site_id} also live here — they're cheap
metadata operations that don't need their own module.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from server.config import settings
from server.ingestion.pipeline import IngestRequest, run_ingest
from server.retrieval.search import bump_corpus_generation
from server.store.chroma import delete_by_site, list_sites
from server.store.sites import delete as delete_site

router = APIRouter()


class IngestPayload(BaseModel):
    url: str = Field(..., description="start URL")
    max_pages: int | None = None
    max_depth: int | None = None
    force: bool = False
    rate: str | None = None
    concurrency: int | None = None
    user_agent: str | None = None


@router.post("/ingest")
async def ingest(payload: IngestPayload) -> EventSourceResponse:
    req = IngestRequest(
        start_url=payload.url,
        max_pages=payload.max_pages if payload.max_pages is not None else settings.crawl_default_max_pages,
        max_depth=payload.max_depth if payload.max_depth is not None else settings.crawl_default_max_depth,
        force=payload.force,
        rate=payload.rate,
        concurrency=payload.concurrency,
        user_agent=payload.user_agent,
    )

    async def stream() -> AsyncIterator[dict]:
        async for evt in run_ingest(req):
            yield {"event": evt["type"], "data": json.dumps(evt)}

    return EventSourceResponse(stream())


@router.get("/sites")
async def sites() -> list[dict]:
    return list_sites()


@router.delete("/sites/{site_id}")
async def remove_site(site_id: str) -> dict:
    if "/" in site_id or not site_id.strip():
        raise HTTPException(400, "invalid site_id")
    deleted = delete_by_site(site_id)
    delete_site(site_id)
    bump_corpus_generation()
    return {"site_id": site_id, "chunks_deleted": deleted}
