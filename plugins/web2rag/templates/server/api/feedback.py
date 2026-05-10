"""Production feedback endpoints — POST /feedback, GET /feedback/summary, GET /feedback/recent.

The widget POSTs a row when the user clicks 👍/👎. The audit's Capability
section + the operator's dashboard read /feedback/summary + /feedback/recent.

Storage is pluggable (server/store/feedback.py); today's backend is
append-only JSONL. The widget doesn't know or care — it talks to this api.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.store.feedback import FeedbackRecord, make_store

router = APIRouter()


class FeedbackPayload(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message_id: str = Field(..., min_length=1, max_length=128)
    vote: Literal["up", "down"]
    site_id: str | None = None
    comment: str = Field(default="", max_length=4000)
    snapshot: dict[str, Any] = Field(default_factory=dict)


@router.post("/feedback")
async def feedback(payload: FeedbackPayload) -> dict:
    record = FeedbackRecord.new(
        session_id=payload.session_id,
        message_id=payload.message_id,
        site_id=payload.site_id,
        vote=payload.vote,
        comment=payload.comment.strip(),
        snapshot=payload.snapshot,
    )
    make_store().append(record)
    return {"ok": True, "timestamp": record.timestamp}


@router.get("/feedback/summary")
async def feedback_summary(since: str | None = Query(default=None, description="ISO 8601 timestamp")) -> dict:
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError as e:
            raise HTTPException(400, f"invalid since timestamp: {e}")
    return make_store().summary(since=since_dt)


@router.get("/feedback/recent")
async def feedback_recent(limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
    rows = make_store().list_recent(limit=limit)
    # Emit dicts so the response shape is JSON-friendly without dataclass coercion.
    from dataclasses import asdict
    return [asdict(r) for r in rows]
