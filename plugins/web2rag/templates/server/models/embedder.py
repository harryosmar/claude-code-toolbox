"""BGE-M3 embedder — bilingual EN + ID dense embeddings.

Loaded once via sentence-transformers and reused for the life of the process.
Free, MIT-licensed, runs on CPU.

Used by:
  - ingestion/pipeline.py   (embed chunks)
  - retrieval/search.py     (embed queries)
  - retrieval/rewrite.py    (HyDE doc embeddings)
  - guards/guard_query.py   (topic-scope cosine vs site centroid)
  - guards/guard_output.py  (per-sentence faithfulness vs retrieved chunks)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

from server.models._memutil import process_memory_mb

log = logging.getLogger(__name__)


class _Embedder:
    """Singleton wrapper around `sentence_transformers.SentenceTransformer`.

    Thread-safe lazy load via a single lock — the rest of the api can call
    .embed() concurrently after init.
    """

    name = "BAAI/bge-m3"
    dim = 1024

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
                return  # someone else won the race
            start = time.monotonic()
            mem_before = process_memory_mb()

            # Imported lazily so importing this module is cheap.
            from sentence_transformers import SentenceTransformer

            log.info("loading embedder %s ...", self.name)
            self._model = SentenceTransformer(self.name, device="cpu")
            self.load_time_s = round(time.monotonic() - start, 2)
            self.memory_mb = max(0, process_memory_mb() - mem_before)
            log.info("embedder loaded in %.1fs (+%d MB RSS)", self.load_time_s, self.memory_mb)

    def embed(self, texts: Iterable[str], *, batch_size: int = 32) -> list[list[float]]:
        """Encode a batch of strings into 1024-dim float vectors."""
        self.load()
        assert self._model is not None
        # convert_to_numpy=True gives us plain numpy; .tolist() makes JSON-friendly.
        vectors = self._model.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


embedder = _Embedder()
