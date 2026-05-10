"""POST /update — delta re-crawl an already-ingested site.

The operator just supplies a site_id; we look up the saved start_url +
ingest options and re-run the pipeline. The pipeline's chunk_hash dedup +
batch-id-based stale-chunk eviction means unchanged pages are no-ops and
removed/changed pages get cleaned up automatically.

Optional body fields override the saved options for one run.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from server.ingestion.pipeline import IngestRequest, run_ingest
from server.store.sites import get as get_site

router = APIRouter()


class UpdatePayload(BaseModel):
    site_id: str
    # all optional — default to whatever the original /ingest used
    max_pages: int | None = None
    max_depth: int | None = None
    force: bool = False
    rate: str | None = None
    concurrency: int | None = None
    user_agent: str | None = None


@router.post("/update")
async def update(payload: UpdatePayload) -> EventSourceResponse:
    cfg = get_site(payload.site_id)
    if cfg is None:
        raise HTTPException(404, f"site {payload.site_id!r} has never been ingested")

    req = IngestRequest(
        start_url=cfg.start_url,
        max_pages=payload.max_pages or cfg.last_max_pages,
        max_depth=payload.max_depth if payload.max_depth is not None else cfg.last_max_depth,
        force=payload.force,
        rate=payload.rate,
        concurrency=payload.concurrency,
        user_agent=payload.user_agent,
    )

    async def stream() -> AsyncIterator[dict]:
        async for evt in run_ingest(req):
            yield {"event": evt["type"], "data": json.dumps(evt)}

    return EventSourceResponse(stream())
