"""Ingestion pipeline — scrape → guard_ingest → chunk → embed → upsert.

Driven by POST /ingest. Streams progress events back to the caller as it works.

Cost contract: nothing in this pipeline calls a paid api. The embedder runs
in-process; chroma persists locally; guard_ingest uses local models added in
build step 4.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

from server.config import settings
from server.guards.guard_ingest import guard_ingest
from server.ingestion.acronyms import extract_acronyms
from server.ingestion.chunker import chunk_markdown
from server.ingestion.normalize import normalize as normalize_markdown
from server.ingestion.politeness import PolitenessPolicy
from server.ingestion.scraper import ScrapedPage, crawl
from server.models.embedder import embedder
from server.retrieval.search import bump_corpus_generation
from server.store.acronyms import merge as merge_acronyms
from server.store.chroma import ChunkMetadata, delete_stale_for_url, upsert_chunks
from server.store.sites import SiteConfig, upsert as upsert_site

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestRequest:
    start_url: str
    max_pages: int = 100
    max_depth: int = 2
    force: bool = False  # ignore robots.txt
    rate: str | None = None
    concurrency: int | None = None
    user_agent: str | None = None


@dataclass
class IngestProgress:
    pages_scraped: int = 0
    pages_dropped_by_guard: int = 0
    chunks_embedded: int = 0
    chunks_deleted: int = 0
    acronyms_learned: int = 0  # net new entries written across all per-site tables
    guard_hits: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.guard_hits is None:
            self.guard_hits = {"low": 0, "med": 0, "high": 0}


async def run_ingest(req: IngestRequest) -> AsyncIterator[dict]:
    """Yield JSON-serialisable progress events as the pipeline runs.

    Final event: {"type": "done", ...summary}.
    """
    policy = PolitenessPolicy(
        respect_robots_txt=settings.respect_robots_txt and not req.force,
        rate_limit=req.rate or settings.crawl_rate_limit,
        concurrency=req.concurrency or settings.crawl_concurrency,
        user_agent=req.user_agent or settings.crawl_user_agent,
    )
    batch_id = str(uuid.uuid4())
    progress = IngestProgress()

    yield {
        "type": "started",
        "policy": {
            "respect_robots_txt": policy.respect_robots_txt,
            "rate_limit": policy.rate_limit,
            "concurrency": policy.concurrency,
            "user_agent": policy.user_agent,
            "force_robots_override": req.force,
        },
        "batch_id": batch_id,
    }

    async for page in crawl(
        req.start_url,
        policy=policy,
        max_pages=req.max_pages,
        max_depth=req.max_depth,
    ):
        verdict = guard_ingest(page)
        progress.guard_hits[verdict.severity] = progress.guard_hits.get(verdict.severity, 0) + (
            1 if not verdict.passed or verdict.severity != "low" else 0
        )
        if not verdict.passed:
            progress.pages_dropped_by_guard += 1
            yield {
                "type": "guard_drop",
                "url": page.url,
                "severity": verdict.severity,
                "reasons": verdict.reasons,
            }
            continue

        # Use the (possibly-redacted) text from the guard's output, then
        # normalise it (strip decorative separators, collapse blank runs).
        # Normalisation is corpus-agnostic — see ingestion/normalize.py for
        # the rule set. Runs BEFORE acronym extraction so the regex doesn't
        # waste time on `/  /  /  /` runs, and BEFORE chunking so chunks
        # don't carry decorative noise into their embeddings.
        raw_md = verdict.redacted_text if verdict.redacted_text is not None else page.markdown
        body_md = normalize_markdown(raw_md)

        # Auto-acronym extraction (B1) — pure regex over the post-guard text.
        # Runs before chunking so definitions split across the boundary
        # (e.g. "Badan Gizi Nasional" at end of one chunk, "(BGN)" at start
        # of the next) are still captured. Generic — works for any corpus
        # whose source defines acronyms with the universal `Full Name (ACRO)`
        # / `ACRO (Full Name)` parenthetical convention. $0 (pure CPU).
        page_acros = extract_acronyms(body_md)
        if page_acros and page.site_id:
            merge_acronyms(page.site_id, page_acros)
            # Per-page event so operators tail-watching /ingest see the
            # extractor at work. The authoritative running total comes
            # from re-reading the file in the final 'done' event below.
            yield {
                "type": "acronyms_extracted",
                "url": page.url,
                "site_id": page.site_id,
                "acronyms": page_acros,
            }

        chunks = chunk_markdown(body_md)
        if not chunks:
            yield {"type": "page", "url": page.url, "chunks": 0}
            progress.pages_scraped += 1
            continue

        embeddings = embedder.embed([c.text for c in chunks])
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        metadatas = [
            ChunkMetadata(
                source_url=page.url,
                site_id=page.site_id,
                section_title=c.section_title,
                chunk_index=c.index,
                chunk_hash=c.hash,
                ingestion_batch_id=batch_id,
                last_updated=now_iso,
                page_title=page.page_title,
                anchor_text=None,
                language=page.language,
            )
            for c in chunks
        ]
        upsert_chunks(chunks=[c.text for c in chunks], embeddings=embeddings, metadatas=metadatas)
        # Evict any chunks that were stored under THIS source_url in a previous
        # batch but didn't appear in this one (deletes / reorganisations).
        deleted = delete_stale_for_url(source_url=page.url, current_batch_id=batch_id)

        progress.pages_scraped += 1
        progress.chunks_embedded += len(chunks)
        progress.chunks_deleted += deleted

        yield {
            "type": "page",
            "url": page.url,
            "chunks": len(chunks),
            "deleted_stale": deleted,
        }

    # Invalidate the BM25 cache so the next /chat rebuilds it with the new chunks.
    if progress.chunks_embedded or progress.chunks_deleted:
        bump_corpus_generation()

    # Persist site config so /web2rag-update can re-crawl with the same start URL.
    if progress.pages_scraped:
        from urllib.parse import urlparse
        site_id = urlparse(req.start_url).netloc
        upsert_site(SiteConfig(
            site_id=site_id,
            start_url=req.start_url,
            last_ingested=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            last_batch_id=batch_id,
            last_max_pages=req.max_pages,
            last_max_depth=req.max_depth,
        ))

    # Sum the acronym tables we updated this batch — convenient for the
    # /ingest summary; the per-site files on disk are already authoritative.
    if progress.pages_scraped:
        from urllib.parse import urlparse
        from server.store.acronyms import load as load_acronyms
        site_id_for_acros = urlparse(req.start_url).netloc
        progress.acronyms_learned = len(load_acronyms(site_id_for_acros))

    yield {
        "type": "done",
        "batch_id": batch_id,
        "pages_scraped": progress.pages_scraped,
        "pages_dropped_by_guard": progress.pages_dropped_by_guard,
        "chunks_embedded": progress.chunks_embedded,
        "chunks_deleted": progress.chunks_deleted,
        "acronyms_total": progress.acronyms_learned,
        "guard_hits": progress.guard_hits,
    }
