"""GET /health — liveness + model-readiness probe."""
from __future__ import annotations

import time

from fastapi import APIRouter

from server.config import settings
from server.guards.detoxify_engine import detoxify
from server.guards.presidio_engine import presidio
from server.guards.prompt_guard import prompt_guard
from server.models.embedder import embedder
from server.models.reranker import reranker

router = APIRouter()
_BOOT_AT = time.monotonic()


def _state(name: str, loaded: bool, load_time_s: float, memory_mb: int) -> dict:
    return {"name": name, "loaded": loaded, "load_time_s": load_time_s, "memory_mb": memory_mb}


@router.get("/health")
async def health() -> dict:
    all_loaded = (
        embedder.loaded
        and reranker.loaded
        and prompt_guard.loaded
        and presidio.loaded
        and detoxify.loaded
    )
    return {
        "status": "ok" if all_loaded else "warming",
        "uptime_s": round(time.monotonic() - _BOOT_AT, 2),
        "chat_model": settings.chat_model,
        "prompt_guard_backend": settings.prompt_guard_backend,
        "respect_robots_txt": settings.respect_robots_txt,
        "crawl_rate_limit": settings.crawl_rate_limit,
        "models": {
            "embedder":     _state(embedder.name, embedder.loaded, embedder.load_time_s, embedder.memory_mb),
            "reranker":     _state(reranker.name, reranker.loaded, reranker.load_time_s, reranker.memory_mb),
            "prompt_guard": _state(prompt_guard.name, prompt_guard.loaded, prompt_guard.load_time_s, prompt_guard.memory_mb),
            "presidio":     _state(presidio.name, presidio.loaded, presidio.load_time_s, presidio.memory_mb),
            "detoxify":     _state(detoxify.name, detoxify.loaded, detoxify.load_time_s, detoxify.memory_mb),
        },
    }
