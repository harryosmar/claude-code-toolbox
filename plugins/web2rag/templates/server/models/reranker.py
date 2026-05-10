"""bge-reranker-v2-m3 — multilingual cross-encoder for retrieval reranking.

Free, MIT-licensed, runs on CPU. Bigger than the embedder per-call (it scores
each (query, candidate) pair) so we keep top_k retrieve small (~25) and
rerank to top_n (~5) before handing chunks to the LLM.

Used by:
  - retrieval/rerank.py  (the only caller).
"""
from __future__ import annotations

import logging
import threading
import time

from server.models._memutil import process_memory_mb

log = logging.getLogger(__name__)


class _Reranker:
    name = "BAAI/bge-reranker-v2-m3"

    def __init__(self) -> None:
        self._model = None  # type: ignore[var-annotated]
        self._lock = threading.Lock()
        self.load_time_s: float = 0.0
        self.memory_mb: int = 0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            start = time.monotonic()
            mem_before = process_memory_mb()

            from sentence_transformers import CrossEncoder

            log.info("loading reranker %s ...", self.name)
            self._model = CrossEncoder(self.name, device="cpu", max_length=512)
            self.load_time_s = round(time.monotonic() - start, 2)
            self.memory_mb = max(0, process_memory_mb() - mem_before)
            log.info("reranker loaded in %.1fs (+%d MB RSS)", self.load_time_s, self.memory_mb)

    def score(
        self,
        query: str,
        candidates: list[str],
        *,
        batch_size: int | None = None,
    ) -> list[float]:
        """Return one relevance score per candidate, in input order.

        `batch_size` controls the cross-encoder forward-pass chunking. If
        None, falls back to settings.rerank_batch_size (default 32). Smaller
        batches reduce peak memory at the cost of slightly less throughput;
        larger batches help on machines with cache/VRAM headroom. With the
        default RETRIEVE_TOP_K=10 all candidates fit in one batch regardless,
        so this knob mostly matters when operators bump top-k.
        """
        self.load()
        assert self._model is not None
        if not candidates:
            return []
        if batch_size is None:
            from server.config import settings  # avoid import cycle at module load
            batch_size = settings.rerank_batch_size
        pairs = [(query, c) for c in candidates]
        scores = self._model.predict(
            pairs,
            batch_size=batch_size,
            show_progress_bar=False,
        )
        return [float(s) for s in scores]


reranker = _Reranker()
