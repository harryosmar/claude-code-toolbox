"""Chroma client + the single `web_corpus` collection.

This file is the SINGLE source of truth for the citation metadata schema.
Everything else in server/ reads/writes through this module; never import
chromadb directly from anywhere else.

Schema (per chunk):
  source_url          — full URL the chunk came from
  site_id             — host of source_url; the unit of /web2rag-list/remove
  section_title       — nearest preceding <h1>/<h2>/<h3>
  chunk_index         — 0-based index within source_url
  chunk_hash          — sha256 of the chunk text; doc id in chroma
  ingestion_batch_id  — uuid rotated per ingest call; lets /update purge stale chunks
  last_updated        — ISO 8601
  page_title          — <title> of the source page
  anchor_text         — closest preceding `id=...` element (for deep-link citations)
  language            — 'en' | 'id' | 'unknown'

See references/citation-schema.md (in the plugin) for end-to-end semantics.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Iterable

import chromadb
from chromadb.config import Settings as ChromaSettings

from server.config import settings

log = logging.getLogger(__name__)

COLLECTION_NAME = "web_corpus"


@dataclass(frozen=True)
class ChunkMetadata:
    source_url: str
    site_id: str
    section_title: str
    chunk_index: int
    chunk_hash: str
    ingestion_batch_id: str
    last_updated: str
    page_title: str
    anchor_text: str | None
    language: str


@lru_cache(maxsize=1)
def _client():
    log.info("connecting to chroma at %s:%d", settings.chroma_host, settings.chroma_port)
    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


@lru_cache(maxsize=1)
def collection():
    """Return the single `web_corpus` collection, creating it on first call.

    We pass embedding_function=None: we provide vectors ourselves (computed by
    server/models/embedder.py) so no chroma-side embedding ever happens —
    that's how we keep ingest at zero cost.
    """
    return _client().get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},
    )


def upsert_chunks(
    *,
    chunks: list[str],
    embeddings: list[list[float]],
    metadatas: Iterable[ChunkMetadata],
) -> None:
    """Upsert. Existing chunks with the same chunk_hash are overwritten."""
    metas = list(metadatas)
    if not (len(chunks) == len(embeddings) == len(metas)):
        raise ValueError("chunks/embeddings/metas length mismatch")
    if not chunks:
        return
    ids = [m.chunk_hash for m in metas]
    collection().upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,  # type: ignore[arg-type]  # chroma's stub uses invariant list; runtime accepts list[list[float]]
        metadatas=[_metadata_dict(m) for m in metas],
    )


def delete_by_site(site_id: str) -> int:
    """Delete every chunk for one site. Returns rows deleted (best-effort)."""
    col = collection()
    before = col.count()
    col.delete(where={"site_id": site_id})
    after = col.count()
    return max(0, before - after)


def delete_stale_for_url(*, source_url: str, current_batch_id: str) -> int:
    """Delete chunks for a URL whose ingestion_batch_id ISN'T the current one.

    Used by the delta re-ingest path: after upserting the new batch's chunks,
    anything still tagged with an older batch on the same source_url has been
    removed from the upstream page and should be evicted.
    """
    col = collection()
    before = col.count()
    col.delete(where={
        "$and": [
            {"source_url": source_url},
            {"ingestion_batch_id": {"$ne": current_batch_id}},
        ]
    })
    after = col.count()
    return max(0, before - after)


def list_sites() -> list[dict]:
    """Aggregate per-site stats from the collection."""
    col = collection()
    # chroma's `.get()` returns everything — fine for the listing scale we expect.
    rows = col.get(include=["metadatas"])
    by_site: dict[str, dict] = {}
    for meta in rows["metadatas"] or []:
        site = str(meta.get("site_id", "unknown"))
        s = by_site.setdefault(site, {"site_id": site, "chunks": 0, "pages": set(), "last_updated": ""})
        s["chunks"] += 1
        s["pages"].add(str(meta.get("source_url", "")))
        last = str(meta.get("last_updated", ""))
        if last > s["last_updated"]:
            s["last_updated"] = last
    out = []
    for s in by_site.values():
        out.append({
            "site_id": s["site_id"],
            "page_count": len(s["pages"]),
            "chunk_count": s["chunks"],
            "last_updated": s["last_updated"],
        })
    out.sort(key=lambda r: r["last_updated"], reverse=True)
    return out


def _metadata_dict(m: ChunkMetadata) -> dict:
    """Chroma rejects None values in metadatas — coerce to empty string."""
    d = asdict(m)
    return {k: ("" if v is None else v) for k, v in d.items()}
