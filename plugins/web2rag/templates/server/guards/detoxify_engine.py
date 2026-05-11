"""Toxicity scorer — `unitary/multilingual-toxic-xlm-roberta` via transformers directly.

Why not the `detoxify` PyPI package: it pins `transformers<4.40` which
conflicts with sentence-transformers >= 3.2. We use detoxify's multilingual
checkpoint (`unitary/multilingual-toxic-xlm-roberta`, XLM-RoBERTa base
fine-tuned on Jigsaw multilingual) but call transformers directly so
we can keep both libraries on the same modern transformers version.

Bahasa Indonesia is in-distribution for this checkpoint (XLM-R covers ID
natively); the older `unitary/toxic-bert` is English-only and unsafe for
ID-primary corpora. Label set is the detoxify-standard 6-way:
toxicity, severe_toxicity, obscene, threat, insult, identity_attack — all
probabilities in [0,1].
"""
from __future__ import annotations

import logging
import threading
import time

from server.config import settings
from server.models._memutil import process_memory_mb

log = logging.getLogger(__name__)


class _Detoxify:
    name = "unitary/multilingual-toxic-xlm-roberta"

    @property
    def threshold(self) -> float:
        return settings.toxicity_threshold

    def __init__(self) -> None:
        self._tokenizer = None  # type: ignore[var-annotated]
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

            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            log.info("loading toxicity model %s ...", self.name)
            self._tokenizer = AutoTokenizer.from_pretrained(self.name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self.name)
            self._model.eval()  # type: ignore[union-attr]
            self.load_time_s = round(time.monotonic() - start, 2)
            self.memory_mb = max(0, process_memory_mb() - mem_before)
            log.info("toxicity model loaded in %.1fs (+%d MB RSS)", self.load_time_s, self.memory_mb)

    def score(self, text: str) -> dict[str, float]:
        """Return per-category probabilities. Truncates long inputs to 512 tokens."""
        self.load()
        assert self._tokenizer is not None and self._model is not None

        import torch

        inputs = self._tokenizer(text, return_tensors="pt", truncation=True, max_length=settings.toxicity_max_seq_len)
        with torch.no_grad():
            logits = self._model(**inputs).logits[0]
        # toxic-bert: per-label sigmoid (multi-label classification).
        probs = torch.sigmoid(logits).tolist()
        labels = list(self._model.config.id2label.values())  # type: ignore[union-attr]
        return {labels[i]: float(probs[i]) for i in range(len(labels))}

    def is_toxic(self, text: str) -> tuple[bool, list[str]]:
        scores = self.score(text)
        bad = [k for k, v in scores.items() if v >= self.threshold]
        return bool(bad), bad


detoxify = _Detoxify()
