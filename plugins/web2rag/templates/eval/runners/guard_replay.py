"""Replay the 3 guards over the test set + adversarial prompts.

Reports per-guard hit-rate (% of inputs that triggered the guard) and the
per-guard severity distribution. Useful for tuning thresholds.
"""
from __future__ import annotations

from typing import Any

from server.guards.guard_ingest import guard_ingest
from server.guards.guard_output import filter_output
from server.guards.guard_query import guard_query
from server.ingestion.scraper import ScrapedPage


def replay_query(prompts: list[str], *, site_id: str | None = None) -> dict[str, Any]:
    by_severity = {"low": 0, "med": 0, "high": 0}
    blocked = 0
    for p in prompts:
        v = guard_query(p, site_id=site_id)
        by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
        if not v.passed:
            blocked += 1
    return {"n": len(prompts), "blocked": blocked, "by_severity": by_severity}


def replay_ingest(pages: list[ScrapedPage]) -> dict[str, Any]:
    by_severity = {"low": 0, "med": 0, "high": 0}
    dropped = 0
    for p in pages:
        v = guard_ingest(p)
        by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
        if not v.passed:
            dropped += 1
    return {"n": len(pages), "dropped": dropped, "by_severity": by_severity}


def replay_output(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """samples: [{"answer": str, "citations": [...], "hits": [Hit, ...]}]"""
    by_severity = {"low": 0, "med": 0, "high": 0}
    blocked = 0
    for s in samples:
        v, _, _ = filter_output(answer=s["answer"], citations=s.get("citations", []), hits=s["hits"])
        by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
        if not v.passed:
            blocked += 1
    return {"n": len(samples), "blocked": blocked, "by_severity": by_severity}
