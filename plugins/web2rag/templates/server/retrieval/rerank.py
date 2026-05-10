"""Cross-encoder rerank over hybrid_search results.

Why two-stage: hybrid retrieval is fast at top-k=25 but noisy. The reranker
is per-pair expensive but has higher signal — we pay the cost on a small
candidate set, then keep only top_n for the LLM.
"""
from __future__ import annotations

from server.models.reranker import reranker
from server.retrieval.search import Hit


def rerank(query: str, hits: list[Hit], *, top_n: int) -> list[Hit]:
    if not hits:
        return []
    scores = reranker.score(query, [h.text for h in hits])
    rescored = [Hit(chunk_id=h.chunk_id, text=h.text, metadata=h.metadata, score=s) for h, s in zip(hits, scores)]
    rescored.sort(key=lambda h: h.score, reverse=True)
    return rescored[:top_n]
