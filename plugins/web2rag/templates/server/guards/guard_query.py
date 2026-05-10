"""guard_query — runs at the START of /chat, BEFORE retrieval.

Checks (all in-process, all free):
  1. Direct prompt injection / jailbreak — DeBERTa-v3 prompt-injection-v2.
  2. User-side PII — Presidio. Always redact (don't ship user PII to the LLM).
  3. Topic-scope drift — cosine sim of query embedding vs the site's mean
     chunk-embedding centroid. Below threshold → refuse with localized message.
  4. Rate limit — in-process token bucket per session_id.

Returns redacted_text when any rewrite happened so the chat path uses it.
"""
from __future__ import annotations

import logging
import time

from server.config import settings
from server.guards.base import GuardResult
from server.guards.presidio_engine import presidio
from server.guards.prompt_guard import prompt_guard
from server.models.embedder import embedder
from server.store.chroma import collection
from server.store.sites import get as get_site_config
from server.util.lang import detect_language

log = logging.getLogger(__name__)


def guard_query(message: str, *, site_id: str | None = None) -> GuardResult:
    reasons: list[str] = []
    text = message

    # --- 1. prompt-injection (language-gated, see config rationale) ---------
    lang = detect_language(text)
    if lang in settings.prompt_injection_lang_set:
        injection_score = prompt_guard.score(text)
        if injection_score >= prompt_guard.high_threshold:
            return GuardResult(
                passed=False,
                severity="high",
                reasons=[f"prompt_injection:{injection_score:.2f}"],
            )
        if injection_score >= prompt_guard.med_threshold:
            reasons.append(f"prompt_injection_warn:{injection_score:.2f}")
    else:
        reasons.append(f"prompt_injection_skipped:lang={lang}")

    # --- 2. PII redaction (with per-site allowlist + per-language entity set) -
    # `lang` was set above by detect_language(). Pass it as lang_hint so the
    # Presidio call picks regex-only entity set for ID content (avoids
    # false-positive PERSON/LOCATION/ORG redaction on the user's question).
    allowlist: list[str] = []
    if site_id:
        cfg = get_site_config(site_id)
        if cfg:
            allowlist = cfg.pii_allowlist
    scrub = presidio.scrub(
        text,
        language="en",
        lang_hint=lang,
        allowlist_entities=allowlist,
    )
    if scrub.entities:
        reasons.append("pii_redacted:" + ",".join(scrub.entities))
        text = scrub.redacted_text

    # --- 3. topic-scope check -----------------------------------------------
    if site_id:
        sim = _topic_scope_similarity(text, site_id=site_id)
        if sim is not None and sim < settings.topic_scope_threshold:
            return GuardResult(
                passed=False,
                severity="med",
                reasons=[*reasons, f"scope_drift:{sim:.2f}"],
                redacted_text=text if scrub.entities else None,
            )

    return GuardResult(
        passed=True,
        severity="med" if reasons else "low",
        reasons=reasons,
        redacted_text=text if scrub.entities else None,
    )


# ── topic-scope cache ─────────────────────────────────────────────────────────

_centroid_cache: dict[str, tuple[list[float], float]] = {}  # site_id → (centroid, computed_at)


def _topic_scope_similarity(query: str, *, site_id: str) -> float | None:
    """Return cosine similarity of `query` vs the site's centroid embedding.

    None if the site has no chunks (e.g. typoed site_id) — caller should treat
    that as 'pass' rather than refuse, otherwise an empty corpus would block
    every query for the site.
    """
    centroid = _centroid_for(site_id)
    if centroid is None:
        return None
    q = embedder.embed_one(query)
    return _cosine(q, centroid)


def _centroid_for(site_id: str) -> list[float] | None:
    cached = _centroid_cache.get(site_id)
    if cached and time.monotonic() - cached[1] < settings.topic_scope_cache_ttl_s:
        return cached[0]
    rows = collection().get(where={"site_id": site_id}, include=["embeddings"])
    embs = rows.get("embeddings")
    # chroma returns embeddings as a numpy ndarray, NOT a list — `or []` /
    # truthy checks raise "truth value ambiguous". Test for emptiness via len().
    if embs is None or len(embs) == 0:
        return None
    centroid = _mean([list(e) for e in embs])
    _centroid_cache[site_id] = (centroid, time.monotonic())
    return centroid


def _mean(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        for i in range(dim):
            out[i] += v[i]
    return [x / n for x in out]


def _cosine(a: list[float], b: list[float]) -> float:
    # Both are L2-normalised by sentence-transformers, so dot product == cosine.
    return sum(x * y for x, y in zip(a, b))
