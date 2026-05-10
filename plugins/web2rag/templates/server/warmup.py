"""Warmup for every heavy in-process model.

Lives outside server/models/ on purpose: importing the guards from
server/models/__init__.py creates a cycle (the guards import _memutil from
server/models, which triggers __init__.py mid-load).

Loads STRICTLY SEQUENTIALLY rather than in parallel: every model touches
torch / transformers / sentence-transformers at import time, and concurrent
threads doing `from transformers import X` while another thread is still
initialising the package can hit a partial-module ImportError. Serialised
loads avoid that race; the cost is +20-40s on first boot, which only
happens once because subsequent boots reuse the cached singletons + the
HuggingFace cache volume.
"""
from __future__ import annotations

import asyncio
import logging

from server.guards.detoxify_engine import detoxify
from server.guards.presidio_engine import presidio
from server.guards.prompt_guard import prompt_guard
from server.models.embedder import embedder
from server.models.reranker import reranker

log = logging.getLogger(__name__)


async def warmup_all() -> None:
    """Load every heavy model. Idempotent.

    Order is intentional: the four torch-stack models go first (one at a time
    to dodge the concurrent-import race on `transformers`), then Presidio at
    the end since it's CPU/IO-only and doesn't share the torch import surface.
    Total: 5 sequential loads, ~30-60s on first boot, ~5s once HF cache is warm.
    """
    log.info("warming up models (embedder + reranker + prompt_guard + detoxify + presidio)")

    for loader in (embedder.load, reranker.load, prompt_guard.load, detoxify.load, presidio.load):
        await asyncio.to_thread(loader)

    log.info(
        "models ready  embedder=%s (%.1fs)  reranker=%s (%.1fs)  prompt_guard=%s (%.1fs)  "
        "detoxify=%s (%.1fs)  presidio (%.1fs)",
        embedder.name, embedder.load_time_s,
        reranker.name, reranker.load_time_s,
        prompt_guard.name, prompt_guard.load_time_s,
        detoxify.name, detoxify.load_time_s,
        presidio.load_time_s,
    )
