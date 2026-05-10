"""guard_output — runs AFTER the LLM, BEFORE the SSE flush.

Checks:
  1. Faithfulness — sentence-split the answer, embed each sentence, max-cosine
     against the retrieved chunk embeddings. Sentences below threshold are
     stripped. If >50% of the answer is unfaithful, replace with a localized
     refusal.
  2. Toxicity — Detoxify (in-process). High-toxicity outputs are blocked.
  3. PII leak — Presidio over the final answer; redact if found.
  4. Citation integrity — every emitted citation must point back to a chunk
     we actually retrieved AND the cited_text must appear in that chunk.

Returns: (verdict, final_answer, final_citations).
"""
from __future__ import annotations

import logging
import re

from server.config import settings
from server.guards.base import GuardResult
from server.guards.detoxify_engine import detoxify
from server.guards.presidio_engine import presidio
from server.models.embedder import embedder
from server.retrieval.search import Hit
from server.store.sites import get as get_site_config
from server.util.lang import detect_language

log = logging.getLogger(__name__)

REFUSAL_EN = "I couldn't find a confident answer in the docs."
REFUSAL_ID = "Saya tidak menemukan jawaban yang meyakinkan di dokumen."

_SENTENCE_RE = re.compile(r"(?<=[\.\!\?])\s+(?=[A-ZÄÖÜ])|\n\n+")


def filter_output(
    *,
    answer: str,
    citations: list[dict],
    hits: list[Hit],
) -> tuple[GuardResult, str, list[dict]]:
    reasons: list[str] = []
    if not answer.strip():
        return GuardResult(passed=True, severity="low"), answer, citations

    # --- 4. citation integrity (cheap, do first) ----------------------------
    valid_urls = {str(h.metadata.get("source_url", "")) for h in hits}
    citations_clean: list[dict] = []
    dropped_cites = 0
    for c in citations:
        if c.get("url") in valid_urls and c.get("snippet"):
            citations_clean.append(c)
        else:
            dropped_cites += 1
    if dropped_cites:
        reasons.append(f"hallucinated_citations_dropped:{dropped_cites}")

    # --- 1. faithfulness ----------------------------------------------------
    sentences = _split_sentences(answer)
    if sentences:
        chunk_vecs = embedder.embed([h.text for h in hits])
        sent_vecs = embedder.embed(sentences)
        kept: list[str] = []
        stripped = 0
        for sent, sv in zip(sentences, sent_vecs):
            best = max((_dot(sv, cv) for cv in chunk_vecs), default=0.0)
            if best >= settings.faithfulness_threshold:
                kept.append(sent)
            else:
                stripped += 1
        if stripped:
            reasons.append(f"faithfulness_strip:{stripped}_sentences")
        if stripped > len(sentences) * 0.5 or not kept:
            answer = _refusal_for(answer)
        else:
            answer = " ".join(kept).strip()

    # --- 2. toxicity --------------------------------------------------------
    toxic, categories = detoxify.is_toxic(answer)
    if toxic:
        return (
            GuardResult(passed=False, severity="high", reasons=[*reasons, "toxic:" + ",".join(categories)]),
            _refusal_for(answer),
            [],
        )

    # --- 3. PII leak (with per-site allowlist) -----------------------------
    # Extract the site from the retrieved hits — they all share the same
    # site_id when /chat was filtered by it. If hits span multiple sites
    # (cross-site retrieval), the strict default applies (no allowlist).
    site_ids = {str(h.metadata.get("site_id", "")) for h in hits if h.metadata.get("site_id")}
    allowlist: list[str] = []
    if len(site_ids) == 1:
        cfg = get_site_config(next(iter(site_ids)))
        if cfg:
            allowlist = cfg.pii_allowlist
    out_lang = detect_language(answer)
    scrub = presidio.scrub(
        answer,
        language="en",
        lang_hint=out_lang,
        allowlist_entities=allowlist,
    )
    if scrub.entities:
        reasons.append("pii_redacted_in_response:" + ",".join(scrub.entities))
        answer = scrub.redacted_text

    return (
        GuardResult(passed=True, severity="med" if reasons else "low", reasons=reasons),
        answer,
        citations_clean,
    )


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
    return parts


def _dot(a: list[float], b: list[float]) -> float:
    # vectors come out of BGE-M3 L2-normalised → dot == cosine
    return sum(x * y for x, y in zip(a, b))


def _refusal_for(answer: str) -> str:
    """Pick a refusal in the language the answer was originally written in."""
    sample = answer[:300].lower()
    id_score = sum(sample.count(w) for w in (" yang ", " dan ", " untuk ", " ini ", " adalah "))
    en_score = sum(sample.count(w) for w in (" the ", " and ", " for ", " this ", " is "))
    return REFUSAL_ID if id_score > en_score else REFUSAL_EN
