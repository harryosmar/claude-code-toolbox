"""guard_ingest — per-page check that runs BEFORE chunking.

Goal: stop attacker-planted instructions and PII from poisoning the corpus.

Checks (all in-process, all free):
  1. Indirect prompt injection — DeBERTa-v3 prompt-injection-v2.
     score ≥ 0.9 → severity=high (drop the page).
     score ≥ 0.5 → severity=med  (continue with a flag in audit log).
  2. PII — Presidio. Always redact; never block solely on PII (legitimate
     pages mention emails/phones; we just don't want them in the index).
  3. Size — already enforced by the scraper (MAX_BYTES). Belt-and-suspenders
     check here too in case the scraper config changes.
  4. Cross-site link smuggling — strip outbound links from the markdown so
     citation rendering can't be tricked into linking off-site.

The (possibly-rewritten) text comes back via redacted_text; the pipeline
embeds that, not the original.
"""
from __future__ import annotations

import logging
import re

from server.config import settings
from server.guards.base import GuardResult
from server.guards.presidio_engine import presidio
from server.guards.prompt_guard import prompt_guard
from server.ingestion.scraper import ScrapedPage
from server.store.sites import get as get_site_config

log = logging.getLogger(__name__)


def guard_ingest(page: ScrapedPage) -> GuardResult:
    reasons: list[str] = []
    text = page.markdown

    # --- 1. size sanity -------------------------------------------------------
    if len(text.encode("utf-8")) > settings.crawl_max_page_bytes:
        return GuardResult(passed=False, severity="high", reasons=["size_exceeded"])

    # --- 2. cross-site link smuggling ----------------------------------------
    text, stripped = _strip_offsite_markdown_links(text, site_id=page.site_id)
    if stripped:
        reasons.append(f"offsite_links_stripped:{stripped}")

    # --- 3. prompt-injection detection (language-gated) ----------------------
    # The English-trained DeBERTa-v3 model false-positives heavily on other
    # languages, so we only run it on languages the operator has explicitly
    # marked safe (PROMPT_INJECTION_LANGUAGES). For everything else we skip
    # the check and tag the page so the audit log knows why.
    if page.language in settings.prompt_injection_lang_set:
        injection_score = prompt_guard.score(text)
        if injection_score >= prompt_guard.high_threshold:
            return GuardResult(
                passed=False,
                severity="high",
                reasons=[*reasons, f"prompt_injection:{injection_score:.2f}"],
            )
        if injection_score >= prompt_guard.med_threshold:
            reasons.append(f"prompt_injection_warn:{injection_score:.2f}")
    else:
        reasons.append(f"prompt_injection_skipped:lang={page.language}")

    # --- 4. PII redaction (with per-site allowlist + per-language entity set) -
    # Pass page.language as lang_hint so Presidio picks the right entity-set
    # default (regex-only for ID; full NER for EN). The actual spaCy model
    # stays English — `language="en"` — because that's the only one we ship.
    cfg = get_site_config(page.site_id)
    allowlist = cfg.pii_allowlist if cfg else []
    scrub = presidio.scrub(
        text,
        language="en",
        lang_hint=page.language,
        allowlist_entities=allowlist,
    )
    if scrub.entities:
        reasons.append("pii_redacted:" + ",".join(scrub.entities))

    return GuardResult(
        passed=True,
        severity="med" if reasons else "low",
        reasons=reasons,
        redacted_text=scrub.redacted_text if scrub.entities or stripped else None,
    )


# Match `[text](url)` markdown links, capturing the url so we can decide.
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")


def _strip_offsite_markdown_links(md: str, *, site_id: str) -> tuple[str, int]:
    """Replace off-site markdown links with their visible text. Returns
    (rewritten_md, count_of_stripped_links).
    """
    n = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal n
        text, url = match.group(1), match.group(2)
        host = re.sub(r"^https?://", "", url).split("/", 1)[0]
        # Match the eTLD+1 loosely: same host or subdomain of site_id.
        if host == site_id or host.endswith("." + site_id):
            return match.group(0)
        n += 1
        return text

    return _MD_LINK.sub(replace, md), n


