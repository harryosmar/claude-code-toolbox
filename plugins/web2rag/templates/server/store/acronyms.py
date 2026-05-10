"""Per-site acronym table — file-backed key/value store.

One JSON file per site at `data/acronyms/<site_id>.json`. The ingestion
pipeline writes to it (server.ingestion.acronyms.extract_acronyms) and
retrieval reads it (server.retrieval.search.expand_query).

Why a separate file per site, not one combined file:
  - Sites are independent corpora. A finance-doc site's acronyms ("EBITDA"
    expanding to "Earnings Before Interest, Taxes, Depreciation, and
    Amortization") have nothing to do with a healthcare site's acronyms.
    Per-site storage keeps the namespaces clean.
  - /web2rag-remove-site nukes one file, no cleanup pass needed.
  - Operators can hand-edit one site's table without touching others.

The table shape is exactly what server.ingestion.acronyms emits:
    {"BGN": "Badan Gizi Nasional", "MBG": "Makan Bergizi", ...}

Concurrency: writes are protected by a process-local lock. Across processes
(uvicorn workers, cron jobs, the backfill helper) we use atomic-rename via
NamedTemporaryFile + os.replace. That's safe for the small JSON payload —
no fsync needed because losing a partial write to crash means the previous
table is still on disk.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_DIR = Path("data/acronyms")
_lock = threading.Lock()


def _path_for(site_id: str) -> Path:
    """Sanitise the site_id for use as a filename. We only allow chars that
    are safe across linux + macOS + windows filesystems; anything else gets
    replaced. Sites are normally domain names so this is rarely needed."""
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in site_id)
    return _DIR / f"{safe}.json"


def load(site_id: str) -> dict[str, str]:
    """Read the table for one site. Returns {} if the file is missing or
    malformed — callers must treat empty as 'no expansion possible' which
    is the sane fallback (retrieval just runs the original query)."""
    p = _path_for(site_id)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        log.warning("acronyms file for %s is not a dict, ignoring", site_id)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("acronyms load for %s failed: %s", site_id, e)
    return {}


def load_all() -> dict[str, str]:
    """Union of every site's table. Used when /retrieve runs without a
    site_id filter (cross-site queries get the benefit of every acronym
    we've ever extracted). On collision (same acronym, different full
    forms across sites), first-seen wins."""
    if not _DIR.exists():
        return {}
    out: dict[str, str] = {}
    for f in _DIR.iterdir():
        if not f.suffix == ".json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    out.setdefault(str(k), str(v))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def save(site_id: str, table: dict[str, str]) -> None:
    """Atomic write of the full table for one site. Overwrites whatever
    was there. Use `merge` instead when you want to add to an existing
    table (the common ingest case)."""
    p = _path_for(site_id)
    with _lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(table, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)


def merge(site_id: str, additions: dict[str, str]) -> dict[str, str]:
    """Merge `additions` into the existing per-site table. First-write
    wins on conflict (existing entries are NOT overwritten) — formal
    docs typically define an acronym once at the canonical location and
    re-mention it informally elsewhere; we want the first definition.

    Returns the resulting full table for caller convenience."""
    if not additions:
        return load(site_id)
    with _lock:
        existing = load(site_id)
        merged = dict(existing)
        for k, v in additions.items():
            merged.setdefault(k, v)
        if merged != existing:
            p = _path_for(site_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(tmp, p)
        return merged


def delete(site_id: str) -> None:
    """Remove a site's acronym file. Called by /web2rag-remove-site."""
    p = _path_for(site_id)
    with _lock:
        if p.exists():
            p.unlink()


__all__ = ["load", "load_all", "save", "merge", "delete"]
