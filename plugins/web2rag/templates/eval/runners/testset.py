"""Test-set generation + caching, via DeepEval's Synthesizer.

Strategy:
  1. Load hand-curated SEEDS from `eval/testsets/_seeds/<site_id>.json`
     if present. These are operator-authored cases that test specific
     known-failure modes (e.g. "apa itu SSPG" — typo robustness across
     SSPG vs SPPG; "what is the partner-registration flow" — bilingual
     procedural recall).
  2. Load (or generate) synthesized cases via DeepEval's Synthesizer
     into `eval/testsets/<site_id>.json`.
  3. MERGE: seeds first, then synthesized cases that don't duplicate a
     seed question. Seeds always run regardless of whether the cache
     exists, so adding a new seed file makes the next audit pick it up
     without --regenerate.

The cache is per-site and content-addressed by site_id — re-ingesting a
site doesn't auto-invalidate the testset (use --regenerate).

We use DeepEval (not RAGAS) for parity with the rest of the eval stack.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from eval.judges.factory import JudgeLLM
from eval.runners._adapters import DeepEvalAdapter
from server.config import settings
from server.store.chroma import collection

log = logging.getLogger(__name__)

CACHE_DIR = Path("eval/testsets")
SEEDS_DIR = CACHE_DIR / "_seeds"


def cache_path(site_id: str) -> Path:
    safe = site_id.replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def seeds_path(site_id: str) -> Path:
    safe = site_id.replace("/", "_")
    return SEEDS_DIR / f"{safe}.json"


def _load_seeds(site_id: str) -> list[dict[str, Any]]:
    """Load hand-curated cases (operator-authored) for this site."""
    p = seeds_path(site_id)
    if not p.exists():
        return []
    try:
        seeds = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("seeds file %s unreadable, skipping: %s", p, e)
        return []
    if not isinstance(seeds, list):
        log.warning("seeds file %s is not a list, skipping", p)
        return []
    # Tag each seed so the per-sample report can distinguish hand-curated
    # cases from synthesised ones during analysis.
    for s in seeds:
        s.setdefault("source", "seed")
    log.info("loaded %d seed case(s) from %s", len(seeds), p)
    return seeds


def _merge(seeds: list[dict[str, Any]], synth: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Seeds win on duplicate questions — they're authoritative."""
    seen = {(s.get("question") or "").strip().lower() for s in seeds if s.get("question")}
    deduped_synth = [
        c for c in synth if (c.get("question") or "").strip().lower() not in seen
    ]
    for c in deduped_synth:
        c.setdefault("source", "synthesized")
    return seeds + deduped_synth


def load_or_generate(
    site_id: str,
    *,
    judge: JudgeLLM,
    size: int,
    locales: list[str],
    regenerate: bool = False,
) -> list[dict[str, Any]]:
    seeds = _load_seeds(site_id)

    p = cache_path(site_id)
    if p.exists() and not regenerate:
        synth = json.loads(p.read_text(encoding="utf-8"))
        return _merge(seeds, synth)

    log.info("generating testset for %s (size=%d, locales=%s)", site_id, size, locales)
    synth = _generate_via_deepeval(site_id=site_id, judge=judge, size=size)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(synth, indent=2, ensure_ascii=False), encoding="utf-8")
    return _merge(seeds, synth)


def _generate_via_deepeval(*, site_id: str, judge: JudgeLLM, size: int) -> list[dict[str, Any]]:
    """Generate Q&A pairs via DeepEval Synthesizer.

    Returns a list of {question, ground_truth, contexts}. We accept that
    DeepEval's locale handling is loose — generated questions track the
    chunks' source language anyway (multilingual BGE-M3 plus mixed-language
    contexts).
    """
    rows = collection().get(where={"site_id": site_id}, include=["documents"])
    docs = [d for d in (rows.get("documents") or []) if d]
    if not docs:
        return []

    try:
        from deepeval.synthesizer import Synthesizer
    except ImportError as e:
        raise RuntimeError("deepeval must be installed") from e

    # Group chunks into context windows. DeepEval expects list[list[str]] —
    # each inner list is the context for one synthetic question.
    window = settings.testset_context_window_chunks
    contexts: list[list[str]] = []
    for i in range(0, len(docs), window):
        contexts.append(docs[i : i + window])

    # Cap the number of contexts to keep the per-context budget aligned with `size`.
    max_per = max(1, size // max(1, len(contexts)))
    if len(contexts) * max_per > size:
        contexts = contexts[: max(1, size // max_per)]

    adapter = DeepEvalAdapter(judge)
    synthesizer = Synthesizer(model=adapter)  # type: ignore[arg-type]
    goldens = synthesizer.generate_goldens_from_contexts(
        contexts=contexts,
        max_goldens_per_context=max_per,
        include_expected_output=True,
    )

    out: list[dict[str, Any]] = []
    for g in goldens:
        out.append(
            {
                "question": getattr(g, "input", "") or "",
                "ground_truth": getattr(g, "expected_output", "") or "",
                "contexts": list(getattr(g, "context", []) or []),
            }
        )
    return out
