"""Tier B inline PII classifier (multilingual: English + Bahasa Indonesia).

Uses Microsoft Presidio. Default backend is spaCy `xx_ent_wiki_sm` (~11 MB,
~30 ms/item on CPU). Optional transformers backend with
`cahya/xlm-roberta-base-indonesian-NER` (~1.1 GB, ~250 ms/item) for higher
Bahasa recall — opt in via config.

Adds custom regex recognizers for Indonesian-specific PII not covered by
NER: NIK (16 digits), NPWP (15 digits w/ dot-dash), BPJS (13 digits).

Public API:
    detect_pii(text, language=...) -> list[PIIEntity]
    redact(text, language=...) -> tuple[str, list[PIIEntity]]

Honors `WORK_INTEL_GUARDS_INGEST=disabled` env var (returns no-op).

Lazy-init: the analyzer engine builds on first call (~2 s warm-up).
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
DEFAULT_LANGUAGES = ("en", "id")
DEFAULT_BACKEND = "spacy"  # vs "transformers"


@dataclass(frozen=True)
class PIIEntity:
    """A single detected PII span."""
    type: str
    start: int
    end: int
    score: float
    text: str


def _kill_switch_active() -> bool:
    return os.environ.get("WORK_INTEL_GUARDS_INGEST", "").lower() == "disabled"


# Lazy globals — built once per process.
_ANALYZER = None
_ANONYMIZER = None
_BACKEND_LOADED: str | None = None


def _build_spacy_engine(languages: tuple[str, ...]):
    """Build a Presidio NLP engine using spaCy multilingual models.

    `xx_ent_wiki_sm` covers ~50 languages including Bahasa Indonesia with
    PERSON / ORG / LOC / MISC labels.
    """
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    # English: small model is enough for PERSON/ORG/LOC; xx_ent_wiki_sm covers
    # everything else including Bahasa.
    models = []
    if "en" in languages:
        models.append({"lang_code": "en", "model_name": "en_core_web_sm"})
    for lang in languages:
        if lang == "en":
            continue
        models.append({"lang_code": lang, "model_name": "xx_ent_wiki_sm"})

    config = {"nlp_engine_name": "spacy", "models": models}
    return NlpEngineProvider(nlp_configuration=config).create_engine()


def _build_transformers_engine(languages: tuple[str, ...]):
    """Build a Presidio NLP engine using HuggingFace transformers per language.

    Requires `presidio-analyzer[transformers]` extra. Uses
    `dslim/bert-base-NER` for English and `cahya/xlm-roberta-base-indonesian-NER`
    for Indonesian.
    """
    from presidio_analyzer.nlp_engine import TransformersNlpEngine

    model_per_lang = {
        "en": {
            "spacy": "en_core_web_sm",
            "transformers": "dslim/bert-base-NER",
        },
        "id": {
            "spacy": "xx_sent_ud_sm",
            "transformers": "cahya/xlm-roberta-base-indonesian-NER",
        },
    }
    models = [
        {"lang_code": lang, "model_name": model_per_lang[lang]}
        for lang in languages if lang in model_per_lang
    ]
    return TransformersNlpEngine(models=models)


def _add_indonesian_recognizers(analyzer) -> None:
    """Custom regex recognizers for Indonesian PII not covered by NER."""
    from presidio_analyzer import Pattern, PatternRecognizer

    nik = PatternRecognizer(
        supported_entity="ID_NIK",
        patterns=[Pattern(name="nik_16digit", regex=r"(?<!\d)\d{16}(?!\d)", score=0.6)],
        context=["nik", "ktp", "identitas"],
        supported_language="id",
    )
    npwp = PatternRecognizer(
        supported_entity="ID_NPWP",
        patterns=[Pattern(
            name="npwp_15digit",
            regex=r"\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}",
            score=0.85,
        )],
        context=["npwp", "wajib pajak", "pajak"],
        supported_language="id",
    )
    bpjs = PatternRecognizer(
        supported_entity="ID_BPJS",
        patterns=[Pattern(name="bpjs_13digit", regex=r"(?<!\d)\d{13}(?!\d)", score=0.55)],
        context=["bpjs", "jaminan", "kesehatan"],
        supported_language="id",
    )
    for r in (nik, npwp, bpjs):
        analyzer.registry.add_recognizer(r)


def _ensure_engines(cfg: dict[str, Any] | None = None) -> None:
    """Lazy-init the analyzer + anonymizer the first time we need them."""
    global _ANALYZER, _ANONYMIZER, _BACKEND_LOADED
    if _ANALYZER is not None:
        return

    cfg = cfg or {}
    pii_cfg = cfg.get("guards", {}).get("ingest", {}).get("pii_classifier", {}) or {}
    languages = tuple(pii_cfg.get("languages") or DEFAULT_LANGUAGES)
    backend = pii_cfg.get("backend") or DEFAULT_BACKEND

    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
    except ImportError as e:
        raise ImportError(
            "presidio-analyzer / presidio-anonymizer not installed. "
            "Run: pip install presidio-analyzer presidio-anonymizer && "
            "python -m spacy download en_core_web_sm && "
            "python -m spacy download xx_ent_wiki_sm"
        ) from e

    if backend == "transformers":
        nlp_engine = _build_transformers_engine(languages)
    else:
        nlp_engine = _build_spacy_engine(languages)

    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=list(languages))
    if "id" in languages:
        _add_indonesian_recognizers(analyzer)

    _ANALYZER = analyzer
    _ANONYMIZER = AnonymizerEngine()
    _BACKEND_LOADED = backend
    log.info("PII classifier initialized: backend=%s languages=%s", backend, languages)


def detect_pii(
    text: str,
    language: str = "en",
    cfg: dict[str, Any] | None = None,
) -> list[PIIEntity]:
    """Return all PII entities found in `text` for the given language."""
    if _kill_switch_active() or not text or not text.strip():
        return []
    _ensure_engines(cfg)
    assert _ANALYZER is not None  # for type checker
    results = _ANALYZER.analyze(text=text, language=language)
    return [
        PIIEntity(
            type=r.entity_type,
            start=r.start,
            end=r.end,
            score=float(r.score),
            text=text[r.start:r.end],
        )
        for r in results
    ]


def redact(
    text: str,
    language: str = "en",
    cfg: dict[str, Any] | None = None,
) -> tuple[str, list[PIIEntity]]:
    """Return (redacted_text, detected_entities). Text length will change."""
    if _kill_switch_active() or not text or not text.strip():
        return text, []

    entities = detect_pii(text, language=language, cfg=cfg)
    if not entities:
        return text, []

    from presidio_analyzer import RecognizerResult
    from presidio_anonymizer.entities import OperatorConfig

    # Per-entity replace operators that include the type in the placeholder.
    seen_types = {e.type for e in entities}
    operators = {t: OperatorConfig("replace", {"new_value": f"[REDACTED:{t}]"}) for t in seen_types}
    operators["DEFAULT"] = OperatorConfig("replace", {"new_value": "[REDACTED]"})

    analyzer_results = [
        RecognizerResult(entity_type=e.type, start=e.start, end=e.end, score=e.score)
        for e in entities
    ]

    assert _ANONYMIZER is not None  # for type checker
    result = _ANONYMIZER.anonymize(text=text, analyzer_results=analyzer_results, operators=operators)
    return result.text, entities


def detect_language(text: str, default: str = "en") -> str:
    """Heuristic language detector for ingest items.

    Returns "id" if the text contains common Indonesian function words (and "en"
    doesn't dominate). Avoids pulling in a heavy language-detection lib.
    Returns `default` when the text is too short to decide.
    """
    if not text or len(text) < 20:
        return default
    lower = text.lower()
    id_markers = (" yang ", " untuk ", " dengan ", " tidak ", " saya ", " kami ", " kita ", " sudah ", " akan ")
    if sum(1 for m in id_markers if m in lower) >= 2:
        return "id"
    return default


def main() -> None:
    """CLI for ad-hoc testing: echo '...' | python _pii_classifier.py [--lang id]."""
    import argparse
    parser = argparse.ArgumentParser(description="PII classifier ad-hoc test.")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--text", help="text to scan; reads stdin if omitted")
    args = parser.parse_args()

    text = args.text if args.text is not None else sys.stdin.read()
    redacted, entities = redact(text, language=args.lang)

    print("=== detected ===")
    for e in entities:
        print(f"  [{e.type}] {e.text!r} ({e.start}-{e.end}, score={e.score:.2f})")
    print("=== redacted ===")
    print(redacted)


if __name__ == "__main__":
    main()
