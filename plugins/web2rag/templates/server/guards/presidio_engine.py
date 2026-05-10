"""Microsoft Presidio analyser + anonymiser, lazy singleton.

Out-of-the-box English recognizers cover EMAIL_ADDRESS, PHONE_NUMBER,
CREDIT_CARD, IBAN_CODE, IP_ADDRESS, URL, CRYPTO, PERSON, LOCATION, etc.

We pin spaCy to `en_core_web_sm` (~12 MB) instead of the default
`en_core_web_lg` (~400 MB). The regex/checksum recognizers are
language-agnostic and catch the bulk of PII either way; PERSON detection
takes a small accuracy hit which is fine for a guard layer.

For Indonesian content we don't ship a separate spaCy model — the
English NER over-fires badly on Indonesian text (every news article
becomes <PERSON>/<LOCATION>/<ORGANIZATION> soup). Instead, callers pass
`lang_hint="id"` and we restrict detection to language-agnostic
regex/checksum entity types via the per-language entity allowlist
configured through PRESIDIO_ID_ENTITIES. For Indonesian-specific PII
(NIK, NPWP) we register custom regex recognizers below — these are
truly language-agnostic and run for both `en` and `id` content.

Tuning surface (all in server/config.py, all env-overridable):
  - PRESIDIO_DISABLED              kill-switch, returns input unchanged
  - PRESIDIO_EN_ENTITIES           comma-sep list; empty = full default set
  - PRESIDIO_ID_ENTITIES           comma-sep list; default = regex-only set
  - PRESIDIO_ID_PATTERNS_ENABLED   toggle NIK/NPWP custom recognizers
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from server.config import settings
from server.models._memutil import process_memory_mb

log = logging.getLogger(__name__)


@dataclass
class PIIScrubResult:
    redacted_text: str
    entities: list[str]


class _Presidio:
    name = "microsoft/presidio (spacy=en_core_web_sm)"

    def __init__(self) -> None:
        self._analyzer = None  # type: ignore[var-annotated]
        self._anonymizer = None  # type: ignore[var-annotated]
        self._lock = threading.Lock()
        self.load_time_s: float = 0.0
        self.memory_mb: int = 0

    @property
    def loaded(self) -> bool:
        return self._analyzer is not None

    def load(self) -> None:
        if self._analyzer is not None:
            return
        with self._lock:
            if self._analyzer is not None:
                return
            start = time.monotonic()
            mem_before = process_memory_mb()

            from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from presidio_anonymizer import AnonymizerEngine

            log.info("loading presidio (spacy=en_core_web_sm) ...")
            nlp_config = {
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
            provider = NlpEngineProvider(nlp_configuration=nlp_config)
            nlp_engine = provider.create_engine()
            analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])

            # Indonesian-specific PII: NIK (16-digit citizen ID), NPWP (15-digit
            # tax ID, often formatted XX.XXX.XXX.X-XXX.XXX). Registered under
            # 'en' because spaCy only supports English here, but the regexes
            # are language-agnostic so they fire on both EN and ID inputs as
            # long as the caller selects them via the entities filter.
            if settings.presidio_id_patterns_enabled:
                analyzer.registry.add_recognizer(
                    PatternRecognizer(
                        supported_entity="ID_NIK",
                        patterns=[
                            Pattern(name="nik_16d", regex=r"\b\d{16}\b", score=0.55),
                        ],
                        context=["nik", "ktp", "nomor induk kependudukan"],
                        supported_language="en",
                    )
                )
                analyzer.registry.add_recognizer(
                    PatternRecognizer(
                        supported_entity="ID_NPWP",
                        patterns=[
                            Pattern(
                                name="npwp_formatted",
                                regex=r"\b\d{2}\.\d{3}\.\d{3}\.\d{1}-\d{3}\.\d{3}\b",
                                score=0.95,
                            ),
                            Pattern(name="npwp_15d", regex=r"\b\d{15}\b", score=0.5),
                        ],
                        context=["npwp", "nomor pokok wajib pajak"],
                        supported_language="en",
                    )
                )
                log.info("presidio: registered ID_NIK + ID_NPWP recognizers")

            self._analyzer = analyzer
            self._anonymizer = AnonymizerEngine()
            self.load_time_s = round(time.monotonic() - start, 2)
            self.memory_mb = max(0, process_memory_mb() - mem_before)
            log.info("presidio loaded in %.1fs (+%d MB RSS)", self.load_time_s, self.memory_mb)

    def scrub(
        self,
        text: str,
        *,
        language: str = "en",
        lang_hint: str | None = None,
        entities: list[str] | None = None,
        allowlist_entities: list[str] | None = None,
    ) -> PIIScrubResult:
        """Detect + anonymise PII.

        Args:
          text:                input string.
          language:            spaCy/Presidio language code. Currently only
                               "en" is registered; the analyser falls back
                               to "en" if anything else is passed.
          lang_hint:           detected content language ('en' or 'id'), used
                               only to pick the default entity set if
                               `entities` is None. Does NOT change the spaCy
                               model — that's still en_core_web_sm.
          entities:            explicit entity-type allowlist for THIS call.
                               When None, falls back to the per-language
                               default from settings (PRESIDIO_EN_ENTITIES /
                               PRESIDIO_ID_ENTITIES). Empty list ([]) means
                               "skip detection entirely" — fast no-op path.
          allowlist_entities:  per-site allowlist of entity types whose
                               spans are LEFT IN PLACE (not redacted). Use
                               for public-info sites where org/person names
                               are content, not PII to defend.

        Returns PIIScrubResult(redacted_text, entities).
        """
        # Master kill-switch.
        if settings.presidio_disabled:
            return PIIScrubResult(redacted_text=text, entities=[])

        self.load()
        assert self._analyzer is not None and self._anonymizer is not None

        # Resolve the entity filter from explicit arg or per-language default.
        effective_entities: list[str] | None
        if entities is not None:
            effective_entities = entities
        else:
            effective_entities = self._default_entities_for(lang_hint or language)

        # Empty list = caller wants no detection → skip the analyser entirely.
        if effective_entities is not None and len(effective_entities) == 0:
            return PIIScrubResult(redacted_text=text, entities=[])

        try:
            results = self._analyzer.analyze(
                text=text,
                language=language,
                entities=effective_entities,  # None passes through to all defaults
            )
        except Exception:  # noqa: BLE001 — language not registered
            results = self._analyzer.analyze(
                text=text,
                language="en",
                entities=effective_entities,
            )

        # Apply per-site allowlist BEFORE anonymising — these spans stay verbatim.
        if allowlist_entities:
            allow = {e.upper() for e in allowlist_entities}
            results = [r for r in results if r.entity_type.upper() not in allow]

        if not results:
            return PIIScrubResult(redacted_text=text, entities=[])

        anonymised = self._anonymizer.anonymize(text=text, analyzer_results=results)
        types = sorted({r.entity_type for r in results})
        return PIIScrubResult(redacted_text=anonymised.text, entities=types)

    @staticmethod
    def _default_entities_for(lang: str) -> list[str] | None:
        """Pick the default entity-type filter for a given content language.

        Returns:
          None  → use Presidio's full default recognizer set (PERSON, LOC,
                  ORG, plus all regex). Only used when the operator left
                  PRESIDIO_EN_ENTITIES empty.
          [...] → restrict detection to these entity types only.
        """
        lang = (lang or "en").lower()
        if lang == "en":
            ent = settings.presidio_en_entity_set
            return ent if ent else None  # empty config → full default set
        # Anything non-EN (id, unknown, fr, ...) uses the ID filter — this is
        # the regex-only set that avoids spaCy's English NER false positives.
        ent = settings.presidio_id_entity_set
        return ent if ent else None  # empty config → full default set


presidio = _Presidio()
