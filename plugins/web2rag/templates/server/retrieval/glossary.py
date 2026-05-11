"""Bilingual glossary — single source of truth for two artefacts:

  1. The synthetic glossary chunk emitted at end of ingest. Goes into chroma
     with ``site_id`` so it surfaces on retrieval whenever a user query
     mentions one of the corpus acronyms. Same store, same retrieval path,
     same citation flow — but the source_url is virtual (``…/_glossary``)
     to make it clear in the audit trail that this is a derived artefact.

  2. The glossary block injected into the SYSTEM_PROMPT at /chat and
     /retrieve request time. Gives the LLM bilingual context even when
     retrieval surfaced only Indonesian chunks. Bounded to the top entries
     by token budget.

Both consumers go through the same render path, so the operator only has
two levers — the per-site auto-extracted table (``data/acronyms/<site>.json``)
and the bundled English gloss bundle (``static_acronyms_en.json``) — and
they apply identically everywhere.

ID-priority contract:

  - The static English gloss is **additive** — it never replaces the
    Indonesian expansion. The Indonesian text always comes first, the
    English gloss is rendered in parentheses after it.
  - For corpus-specific acronyms with no static English entry, the line
    is Indonesian-only. The multilingual LLM derives English meaning at
    response time from the Indonesian text.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from server.store.acronyms import load as load_acronyms

log = logging.getLogger(__name__)

_STATIC_PATH = Path(__file__).parent / "static_acronyms_en.json"
_MAX_GLOSSARY_ENTRIES = 50
"""Cap rendered entries so the SYSTEM_PROMPT stays bounded. 50 entries × ~80
chars/line ≈ 4 KB ≈ ~1000 tokens — well within Haiku/Sonnet's prompt cache
threshold. Most corpora have <30 entries; this cap only bites on very large
sites where we'd want to truncate anyway."""


def _load_static_en() -> dict[str, str]:
    if not _STATIC_PATH.exists():
        return {}
    try:
        data = json.loads(_STATIC_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if not k.startswith("_")}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("static_acronyms_en.json unreadable: %s", e)
    return {}


def _merge_static_en(table: dict[str, str], static_en: dict[str, str]) -> list[tuple[str, str, str | None]]:
    """Combine per-site Indonesian expansions with static English glosses.

    Returns a list of ``(acronym, id_expansion, en_gloss_or_None)`` tuples,
    sorted alphabetically by acronym for stable output. Capped at
    ``_MAX_GLOSSARY_ENTRIES``."""
    rows: list[tuple[str, str, str | None]] = []
    for acro in sorted(table.keys()):
        id_expansion = table[acro]
        en_gloss = static_en.get(acro)
        rows.append((acro, id_expansion, en_gloss))
    return rows[:_MAX_GLOSSARY_ENTRIES]


def render_glossary_lines(site_id: str | None) -> list[str]:
    """Render the per-site glossary as a list of markdown bullet lines.

    Empty list when no acronyms have been auto-extracted for the site yet —
    callers branch on falsy to suppress the empty glossary block. ID
    expansion is always present; English gloss only when bundled."""
    if not site_id:
        return []
    table = load_acronyms(site_id)
    if not table:
        return []
    static_en = _load_static_en()
    rows = _merge_static_en(table, static_en)
    lines: list[str] = []
    for acro, id_expansion, en_gloss in rows:
        if en_gloss:
            lines.append(f"- {acro} = {id_expansion} ({en_gloss})")
        else:
            lines.append(f"- {acro} = {id_expansion}")
    return lines


def render_glossary_text(site_id: str | None) -> str:
    """Plain-text glossary block. Used both as system-prompt context and as
    the body of the synthetic chunk written to chroma at end of ingest."""
    lines = render_glossary_lines(site_id)
    if not lines:
        return ""
    return "\n".join(lines)


def synthetic_chunk_text(site_id: str, start_url: str) -> str:
    """Body text of the synthetic glossary chunk that goes into chroma.

    Written in a retrieval-friendly shape: a short bilingual lead paragraph
    so dense + BM25 both have signal, then the bulleted list. The lead
    paragraph mentions ``acronyms`` and ``glossary`` in both English and
    Indonesian so cross-lingual queries (e.g. ``what is X?``, ``apa itu X?``)
    can latch on without needing the literal acronym to be present in the
    other layers of expansion."""
    body = render_glossary_text(site_id)
    if not body:
        return ""
    lead = (
        f"Site glossary / Daftar singkatan untuk {start_url}.\n\n"
        "This page lists the acronyms used throughout this site, with their full "
        "Indonesian expansion. English meanings follow in parentheses where applicable.\n"
        "Halaman ini berisi daftar singkatan yang digunakan di seluruh situs, "
        "dengan kepanjangannya dalam Bahasa Indonesia. Arti dalam Bahasa Inggris "
        "dicantumkan dalam tanda kurung jika tersedia.\n\n"
    )
    return lead + body


def synthetic_chunk_metadata_kwargs(site_id: str, batch_id: str) -> dict:
    """Construct the ``ChunkMetadata`` field set for the synthetic glossary chunk.

    The synthetic chunk is identified by ``source_url`` ending in
    ``/_glossary`` so audit tools can find / re-emit / delete it without
    touching real pages. Removal of the site via ``/web2rag-remove-site``
    cleans it up automatically because the site_id filter matches."""
    body = render_glossary_text(site_id)
    chunk_hash = hashlib.sha256(f"{site_id}::glossary::{body}".encode("utf-8")).hexdigest()
    return {
        "source_url": f"https://{site_id}/_glossary",
        "site_id": site_id,
        "section_title": "Acronyms / Singkatan",
        "chunk_index": 0,
        "chunk_hash": chunk_hash,
        "ingestion_batch_id": batch_id,
        "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "page_title": "Site glossary / Daftar singkatan",
        "anchor_text": None,
        "language": "multi",
    }


__all__ = [
    "render_glossary_lines",
    "render_glossary_text",
    "synthetic_chunk_text",
    "synthetic_chunk_metadata_kwargs",
]
