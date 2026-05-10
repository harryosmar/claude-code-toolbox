"""FastAPI entrypoint for the web2rag-generated api.

Wires in routers and kicks off model warm-up in the background. Each
capability lives in its own module under server/ — the entrypoint just
composes them. See CLAUDE.md for the cost contract and the 3-guard
architecture.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from server.api.audit import router as audit_router
from server.api.chat import router as chat_router
from server.api.feedback import router as feedback_router
from server.api.health import router as health_router
from server.api.ingest import router as ingest_router
from server.api.policy import router as policy_router
from server.api.retrieve import router as retrieve_router
from server.api.update import router as update_router
from server.api.widget_assets import router as widget_router
from server.config import settings
from server.warmup import warmup_all

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server.main")

app = FastAPI(
    title="web2rag-api",
    version="0.1.0",
    description="Generated RAG api with in-process bilingual embeddings + 3-guard architecture.",
)

app.include_router(health_router)
app.include_router(ingest_router)
app.include_router(update_router)
app.include_router(chat_router)
app.include_router(widget_router)
app.include_router(audit_router)
app.include_router(feedback_router)
app.include_router(policy_router)
app.include_router(retrieve_router)


@app.on_event("startup")
async def _on_startup() -> None:
    # Force settings to materialise so misconfiguration crashes at boot.
    _ = settings.api_port

    # Warm up models in the background so /health stays responsive while they load.
    # The first /ingest or /chat will block on these completing if the warm-up
    # hasn't finished yet (the singletons take their own lock).
    asyncio.create_task(_warmup())


async def _warmup() -> None:
    try:
        await warmup_all()
    except Exception:  # noqa: BLE001
        # Don't crash the api if warm-up fails — log and keep serving so /health
        # surfaces the failure to the operator.
        log.exception("model warm-up failed; api keeps serving but ingest/chat will fail until fixed")
