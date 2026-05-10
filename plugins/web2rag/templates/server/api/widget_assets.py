"""GET /widget.js — serves the embeddable widget.
GET /demo     — operator-facing demo page that mounts the widget for the
                first ingested site. Lets developers test the chat in a
                browser without setting up a separate web server.

The i18n bundles are inlined into the JS at request time (single round-trip
for the embed snippet). The api process holds them in memory after first
read; widget.js itself is small and gets cached by the customer's browser.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Query, Response

from server.store.chroma import list_sites

router = APIRouter()

_WIDGET_DIR = Path(__file__).resolve().parent.parent.parent / "widget" / "src"
# demo.html is a sibling of widget/src/, not inside it, so the customer-
# facing widget bundle and the operator-facing demo stay clearly separate.
_DEMO_PATH = Path(__file__).resolve().parent.parent.parent / "widget" / "demo.html"


@lru_cache(maxsize=1)
def _widget_js() -> str:
    src = (_WIDGET_DIR / "widget.js").read_text(encoding="utf-8")
    en = (_WIDGET_DIR / "i18n" / "en.json").read_text(encoding="utf-8").strip()
    id_ = (_WIDGET_DIR / "i18n" / "id.json").read_text(encoding="utf-8").strip()
    # Inline the bundles. JSON is a strict subset of JS object literals so
    # straight substitution works.
    return src.replace("__I18N_EN__", en).replace("__I18N_ID__", id_)


@lru_cache(maxsize=1)
def _demo_html_template() -> str:
    if not _DEMO_PATH.exists():
        return ""
    return _DEMO_PATH.read_text(encoding="utf-8")


@router.get("/widget.js")
async def widget_js() -> Response:
    return Response(
        content=_widget_js(),
        media_type="application/javascript; charset=utf-8",
        headers={"cache-control": "public, max-age=300"},
    )


@router.get("/demo")
async def demo(site_id: str | None = Query(default=None, description="Override the demo's site_id (default: first ingested)")) -> Response:
    """Operator-facing demo page that mounts the widget against this api.

    Picks the first ingested site_id automatically when no override is given,
    so a fresh deployment that just ran /ingest can immediately open
    `http://<host>:<port>/demo` in a browser and start chatting. URLs in the
    demo are relative, so the page works equally well at localhost or behind
    a reverse proxy.

    Returns 503 with a hint when no sites are ingested yet — the widget
    needs a site_id to retrieve from, and we'd rather fail loudly than
    show a chat that always answers "I don't have information".
    """
    template = _demo_html_template()
    if not template:
        return Response(
            content="<!doctype html><h1>Demo not found</h1>"
            "<p>widget/demo.html is missing from this deployment.</p>",
            media_type="text/html; charset=utf-8",
            status_code=500,
        )

    # Pick the site_id: explicit query param wins, else the first ingested
    # site. If the corpus is empty, render a hint page instead of a broken
    # widget.
    chosen = site_id
    if not chosen:
        sites = list_sites()
        if sites:
            chosen = sites[0]["site_id"]
    if not chosen:
        return Response(
            content=(
                "<!doctype html><meta charset=utf-8>"
                "<title>web2rag demo</title>"
                "<style>body{font:14px/1.5 -apple-system,sans-serif;max-width:560px;"
                "margin:64px auto;padding:0 24px;color:#111}</style>"
                "<h1>No sites ingested yet</h1>"
                "<p>Run <code>/web2rag-ingest &lt;url&gt;</code> first, then refresh "
                "this page. The demo widget needs at least one site_id in chroma "
                "before it can retrieve answers.</p>"
            ),
            media_type="text/html; charset=utf-8",
            status_code=503,
        )

    # Render: the only template hole is the site_id. Everything else is
    # static, including the URLs (which are relative on purpose so the demo
    # works at any deployment without per-host config).
    rendered = template.replace("__SITE_ID__", chosen)
    return Response(
        content=rendered,
        media_type="text/html; charset=utf-8",
        # No long-cache here — the demo is for operators, and they want to
        # see fresh changes without hard-refreshing.
        headers={"cache-control": "no-store"},
    )
