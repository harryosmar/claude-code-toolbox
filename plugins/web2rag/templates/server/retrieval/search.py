"""Hybrid retrieval — BM25 (sparse) + dense (BGE-M3 cosine), fused with RRF.

We rebuild the BM25 index lazily when the chroma collection changes (tracked
by an incrementing generation counter set by /ingest, /update, /remove). For
v0.1 we hold the corpus in memory; this is fine up to a few hundred thousand
chunks and matches the "self-hosted plugin" sweet spot.

For larger corpora, swap in `bm25s` (faster) or move sparse retrieval into
chroma-native full-text indexing once it lands.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Iterable

from rank_bm25 import BM25Okapi

from server.models.embedder import embedder
from server.store.chroma import collection

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    text: str
    metadata: dict
    score: float  # fused RRF score; higher = more relevant


_corpus_generation = 0
_lock = threading.Lock()


def bump_corpus_generation() -> None:
    """Called by ingest / update / remove paths so the BM25 cache invalidates."""
    global _corpus_generation
    with _lock:
        _corpus_generation += 1


class _BM25Index:
    """Lazy in-memory BM25 over the current chroma corpus."""

    def __init__(self) -> None:
        self._gen_at_build = -1
        self._bm25: BM25Okapi | None = None
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._metas: list[dict] = []

    def ensure(self) -> None:
        if self._bm25 is not None and self._gen_at_build == _corpus_generation:
            return
        with _lock:
            if self._bm25 is not None and self._gen_at_build == _corpus_generation:
                return
            log.info("rebuilding BM25 index (generation %d)", _corpus_generation)
            data = collection().get(include=["documents", "metadatas"])
            self._ids = list(data["ids"] or [])
            self._docs = [d or "" for d in (data["documents"] or [])]
            self._metas = [dict(m) if m else {} for m in (data["metadatas"] or [])]
            tokenised = [doc.lower().split() for doc in self._docs]
            self._bm25 = BM25Okapi(tokenised) if tokenised else None
            self._gen_at_build = _corpus_generation

    def search(self, query: str, *, top_k: int) -> list[Hit]:
        self.ensure()
        if not self._bm25 or not self._docs:
            return []
        scores = self._bm25.get_scores(query.lower().split())
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [
            Hit(chunk_id=self._ids[i], text=self._docs[i], metadata=self._metas[i], score=float(scores[i]))
            for i in ranked
        ]


_bm25 = _BM25Index()


def search_dense(query: str, *, top_k: int, where: dict | None = None) -> list[Hit]:
    """Cosine search via chroma. Filters by metadata (e.g. {"site_id": X})."""
    q_vec = embedder.embed_one(query)
    res = collection().query(
        query_embeddings=[q_vec],  # type: ignore[arg-type]
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    out: list[Hit] = []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        # cosine distance → similarity score
        out.append(Hit(chunk_id=cid, text=doc or "", metadata=dict(meta or {}), score=1.0 - float(dist)))
    return out


def hybrid_search(
    query: str,
    *,
    top_k: int,
    where: dict | None = None,
    hypothetical: str | None = None,
) -> list[Hit]:
    """Run dense + BM25 in parallel, fuse with Reciprocal Rank Fusion.

    Three layers of vocabulary bridging, each strictly additive (the next
    layer only contributes signal — it never removes hits the previous
    layer would have surfaced):

      1. Original query — what the user typed. Always included.
      2. **Auto-acronym expansion (B1)**: if the corpus's self-extracted
         acronym table contains any term in the query, expanded variants
         are added. Free, deterministic, ~5 ms.
      3. **HyDE (Hypothetical Document Embeddings)**: when the caller
         passes `hypothetical=<llm-generated answer>`, that text is added
         as one more variant. The LLM call itself happens at the API
         handler level (see server/api/retrieve.py) so this function stays
         sync. Pass `None` to disable HyDE for the call.

    Each variant runs hybrid retrieval (BM25 + dense) → RRF — so a chunk
    that ranks well in ANY variant floats up; chunks ranked well in multiple
    variants float higher. /chat (production) and /audit do NOT pass
    `hypothetical` — production stays on the deterministic path; audit
    must be reproducible. Only /retrieve threads HyDE through.

    Cost: at most `max_query_variants` + 1 (the +1 is the hypothetical when
    present) extra round-trips through embedder + chroma + BM25. With
    variants capped at 3 and HyDE optional, this adds ~100-400 ms per query.
    """
    from server.config import settings as _s
    from server.ingestion.acronyms import expand_with_acronyms
    from server.store.acronyms import load as load_acronyms, load_all as load_all_acronyms

    # Per-site table when the caller filters by site_id; union of all site
    # tables otherwise (cross-site queries get the benefit of every acronym
    # we've ever extracted across deployments).
    site_id = (where or {}).get("site_id") if isinstance(where, dict) else None
    table = load_acronyms(site_id) if site_id else load_all_acronyms()

    variants = expand_with_acronyms(query, table, max_variants=_s.max_query_variants)
    if hypothetical and hypothetical.strip():
        # HyDE answer goes in as one more retrieval variant. Truncated at a
        # generous limit because BGE-M3's effective context for retrieval
        # tops out around 8192 tokens; longer hypotheticals add noise without
        # signal. 4 KB of chars is well under that for any realistic answer.
        variants.append(hypothetical.strip()[:4000])

    ranked_lists: list[list[Hit]] = []
    for v in variants:
        dense = search_dense(v, top_k=top_k, where=where)
        sparse = _bm25.search(v, top_k=top_k)
        if where:
            sparse = [h for h in sparse if _matches_where(h.metadata, where)]
        ranked_lists.append(_rrf(dense, sparse, top_k=top_k))

    if len(ranked_lists) == 1:
        return ranked_lists[0]
    # Fuse the per-variant lists with RRF — chunks ranked well in any
    # variant float up; chunks ranked well in multiple float higher.
    return _rrf(*ranked_lists, top_k=top_k)


def _rrf(*ranked_lists: Iterable[Hit], top_k: int, k: int | None = None) -> list[Hit]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank_i)."""
    from server.config import settings as _s
    k = k if k is not None else _s.rrf_k
    scores: dict[str, float] = {}
    by_id: dict[str, Hit] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            by_id.setdefault(hit.chunk_id, hit)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [Hit(chunk_id=cid, text=by_id[cid].text, metadata=by_id[cid].metadata, score=score) for cid, score in fused]


def _matches_where(meta: dict, where: dict) -> bool:
    # Tiny subset of chroma's where syntax — equality checks only. Sparse
    # filtering is a best-effort sanity layer; the dense path is authoritative.
    for k, v in where.items():
        if k.startswith("$"):
            continue
        if meta.get(k) != v:
            return False
    return True
