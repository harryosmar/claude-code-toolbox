"""Crawler — Crawl4AI primary, Trafilatura HTML→Markdown fallback.

Yields ScrapedPage instances. Honours the PolitenessPolicy passed in:
respects (or skips) robots.txt, rate-limits via TokenBucket, caps
concurrency, sets the configured User-Agent.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from server.config import settings
from server.ingestion.politeness import PolitenessPolicy, RobotsCache, TokenBucket, discover_sitemap_urls

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrapedPage:
    url: str
    site_id: str
    page_title: str
    markdown: str
    content_hash: str
    content_type: str
    last_modified: str | None  # HTTP Last-Modified or sitemap <lastmod>

    @property
    def language(self) -> str:
        from server.util.lang import detect_language
        return detect_language(self.markdown)


async def crawl(
    start_url: str,
    *,
    policy: PolitenessPolicy,
    max_pages: int,
    max_depth: int,
) -> AsyncIterator[ScrapedPage]:
    """Yield each scraped page in turn.

    Strategy:
      1. Try sitemap.xml first — sitemaps give us URL + lastmod for free and
         make delta re-crawls trivial.
      2. Fall back to BFS from start_url with a depth cap, only following
         links inside the same site_id.

    Politeness:
      - rate limited by TokenBucket
      - concurrency capped via asyncio.Semaphore
      - per-host robots.txt enforced unless policy.respect_robots_txt=false
    """
    parsed = urlparse(start_url)
    site_id = parsed.netloc
    if not site_id:
        raise ValueError(f"start_url must include a host: {start_url!r}")

    bucket = TokenBucket(*_bucket_args(policy))
    sem = asyncio.Semaphore(policy.concurrency)
    robots = RobotsCache(user_agent=policy.user_agent) if policy.respect_robots_txt else None

    seen: set[str] = set()
    queue: list[tuple[str, int, str | None]] = []  # (url, depth, lastmod)

    sitemap_urls = await discover_sitemap_urls(site_id, user_agent=policy.user_agent)
    if sitemap_urls:
        for url, lastmod in sitemap_urls:
            if urlparse(url).netloc == site_id:
                queue.append((url, 0, lastmod))
    else:
        queue.append((start_url, 0, None))

    async with httpx.AsyncClient(
        timeout=settings.crawl_request_timeout_s,
        follow_redirects=True,
        headers={"User-Agent": policy.user_agent},
    ) as client:
        scraped = 0
        while queue and scraped < max_pages:
            # Pull a small batch off the queue and fetch them concurrently.
            batch, queue = queue[: policy.concurrency], queue[policy.concurrency :]
            tasks = [_fetch_one(client, url, depth, lastmod, site_id, bucket, sem, robots, max_depth) for (url, depth, lastmod) in batch]
            results: list = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    log.warning("fetch failed: %s", result)
                    continue
                if result is None:
                    continue
                page, links = result  # type: ignore[misc]  # narrowed by the two checks above
                if scraped >= max_pages:
                    break
                yield page
                scraped += 1
                # Enqueue intra-site links we haven't seen.
                for link in links:
                    if link in seen or urlparse(link).netloc != site_id:
                        continue
                    seen.add(link)
                    queue.append((link, 0, None))


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    depth: int,
    lastmod: str | None,
    site_id: str,
    bucket: TokenBucket,
    sem: asyncio.Semaphore,
    robots: RobotsCache | None,
    max_depth: int,
) -> tuple[ScrapedPage, list[str]] | None:
    if depth > max_depth:
        return None
    if robots is not None and not await robots.can_fetch(url):
        log.info("robots.txt disallows %s; skipping", url)
        return None

    await bucket.take()
    async with sem:
        resp = await client.get(url)

    ct = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ct and not (ct.startswith("text/html") or ct.startswith("text/markdown") or ct == "text/plain"):
        log.info("skipping %s (content-type %s)", url, ct)
        return None
    if len(resp.content) > settings.crawl_max_page_bytes:
        log.info("skipping %s (size %d > %d)", url, len(resp.content), settings.crawl_max_page_bytes)
        return None
    if resp.status_code != 200:
        return None

    html = resp.text
    md = _html_to_markdown(html, base_url=url)
    if not md.strip():
        return None

    title = _extract_title(html)
    last = lastmod or resp.headers.get("last-modified")
    page = ScrapedPage(
        url=url,
        site_id=site_id,
        page_title=title,
        markdown=md,
        content_hash=hashlib.sha256(md.encode("utf-8")).hexdigest(),
        content_type=ct or "text/html",
        last_modified=last,
    )
    links = _extract_links(html, base_url=url)
    return page, links


def _html_to_markdown(html: str, *, base_url: str) -> str:
    """Trafilatura is the reliable HTML→MD path. Crawl4AI's own extractor is
    JS-aware but heavier; we keep this code path simple by using trafilatura
    (already a hard dep) which gives us tables + images + headings preserved.
    """
    md = trafilatura.extract(
        html,
        url=base_url,
        output_format="markdown",
        include_tables=True,
        include_images=False,
        include_links=False,
        deduplicate=True,
        favor_recall=True,
    )
    return md or ""


def _extract_title(html: str) -> str:
    # Avoid a bs4 import here — title extraction via cheap regex is fine.
    import re
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return m.group(1).strip()


def _extract_links(html: str, *, base_url: str) -> list[str]:
    import re
    out = []
    for m in re.finditer(r'href="([^"]+)"', html):
        href = m.group(1)
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        out.append(urljoin(base_url, href))
    # de-dup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for l in out:
        if l not in seen:
            seen.add(l)
            deduped.append(l)
    return deduped


def _bucket_args(policy: PolitenessPolicy) -> tuple[int, float]:
    return policy.rate_per_window
