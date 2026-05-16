"""Crawl politeness — robots.txt, sitemaps, lastmod tracking, rate limiting.

Defaults are polite. Every switch is overridable via .env or per-/web2rag-ingest
CLI flag (which is forwarded into the api as a request-body field). Override
decisions are recorded so the operator's intent stays auditable.

See CLAUDE.md.tmpl > "Politeness controls" for the matrix.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from defusedxml import ElementTree as ET

import httpx

log = logging.getLogger(__name__)

_RATE_RE = re.compile(r"^(\d+)/(\d+)([smh])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600}


@dataclass(frozen=True)
class PolitenessPolicy:
    """All knobs operators can tune, materialised once per ingest call."""
    respect_robots_txt: bool
    rate_limit: str           # e.g. "10/60s"
    concurrency: int
    user_agent: str

    @property
    def rate_per_window(self) -> tuple[int, float]:
        m = _RATE_RE.fullmatch(self.rate_limit)
        if not m:
            raise ValueError(f"invalid rate_limit {self.rate_limit!r}")
        n, count, unit = int(m.group(1)), int(m.group(2)), m.group(3)
        return n, count * _UNIT_S[unit]


@dataclass
class TokenBucket:
    """Simple async token bucket. Refills `n` tokens every `window` seconds."""
    n: int
    window: float
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.n)
        self._last = time.monotonic()

    async def take(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.n, self._tokens + elapsed * (self.n / self.window))
            self._last = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            wait = (1 - self._tokens) * (self.window / self.n)
        await asyncio.sleep(wait)
        await self.take()


class RobotsCache:
    """Per-host robots.txt cache. One fetch per (host, user_agent) per process."""

    def __init__(self, *, user_agent: str) -> None:
        self.user_agent = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}
        self._lock = asyncio.Lock()

    async def can_fetch(self, url: str) -> bool:
        host = urlparse(url).netloc
        async with self._lock:
            if host not in self._cache:
                self._cache[host] = await self._fetch(host)
        rp = self._cache[host]
        if rp is None:
            # No robots.txt or unfetchable — allow by default (this matches Google's
            # behaviour: missing robots = no restrictions).
            return True
        return rp.can_fetch(self.user_agent, url)

    async def _fetch(self, host: str) -> RobotFileParser | None:
        url = f"https://{host}/robots.txt"
        try:
            async with httpx.AsyncClient(timeout=10, headers={"User-Agent": self.user_agent}) as c:
                r = await c.get(url)
        except Exception:  # noqa: BLE001
            log.info("no robots.txt at %s (network error); allowing all", url)
            return None
        if r.status_code != 200 or not r.text.strip():
            log.info("no robots.txt at %s (status %d); allowing all", url, r.status_code)
            return None
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp


async def discover_sitemap_urls(host: str, *, user_agent: str) -> list[tuple[str, str | None]]:
    """Return [(url, lastmod)] from /sitemap.xml or /sitemap_index.xml.

    Empty list if no sitemap is reachable. Best-effort; a missing sitemap is
    not a fatal condition.
    """
    candidates = [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"]
    found: list[tuple[str, str | None]] = []

    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": user_agent}) as c:
        for sm in candidates:
            try:
                r = await c.get(sm)
            except Exception:  # noqa: BLE001
                continue
            if r.status_code != 200:
                continue
            found.extend(_parse_sitemap_xml(r.text, base=sm))
            if found:
                break
    return found


def _parse_sitemap_xml(xml_text: str, *, base: str) -> list[tuple[str, str | None]]:
    """Parse a sitemap or sitemap-index document into [(url, lastmod)]."""
    out: list[tuple[str, str | None]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    # Strip XML namespace from tag names so the parsing is namespace-agnostic.
    def localname(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag

    if localname(root.tag) == "sitemapindex":
        # Index of nested sitemaps — recurse synchronously is fine here, callers
        # already run inside an async context. We skip recursion for simplicity:
        # callers should fetch each <loc> separately. Returning empty pushes
        # them to the homepage-crawl fallback.
        return out

    for url_el in root:
        if localname(url_el.tag) != "url":
            continue
        loc = ""
        lastmod: str | None = None
        for child in url_el:
            if localname(child.tag) == "loc" and child.text:
                loc = urljoin(base, child.text.strip())
            elif localname(child.tag) == "lastmod" and child.text:
                lastmod = child.text.strip()
        if loc:
            out.append((loc, lastmod))
    return out
