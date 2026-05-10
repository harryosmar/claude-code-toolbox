"""Per-site config — start_url, last_ingested timestamp, last batch_id.

Tiny file-backed key-value store. Lives at data/sites.json so it survives
container restarts (the data/ directory is the persistent volume).

Why not chroma metadata: per-site config is one record per site, not per
chunk. Storing it in chroma would mean either (a) replicating the same
fields onto every chunk (wasteful) or (b) abusing chroma collection-level
metadata in a way that's awkward to update. A flat JSON file is right-sized.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

_PATH = Path("data/sites.json")
_lock = threading.Lock()


@dataclass
class SiteConfig:
    site_id: str
    start_url: str
    last_ingested: str = ""
    last_batch_id: str = ""
    last_max_pages: int = 100
    last_max_depth: int = 2
    # Presidio entity types this site is permitted to PRESERVE (not redact).
    # E.g. for a public-info site, ["PERSON", "ORGANIZATION", "LOCATION"]
    # keeps real names of public officials in the corpus instead of scrubbing
    # them to <PERSON>/<ORG>. Operator-controlled via PUT /sites/{id}/policy.
    pii_allowlist: list[str] = field(default_factory=list)


def _read_all() -> dict[str, dict]:
    if not _PATH.exists():
        return {}
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_all(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _PATH)


def get(site_id: str) -> SiteConfig | None:
    with _lock:
        rec = _read_all().get(site_id)
    if rec is None:
        return None
    # Tolerate older sites.json files that pre-date the pii_allowlist field.
    rec.setdefault("pii_allowlist", [])
    return SiteConfig(**rec)


def upsert(cfg: SiteConfig) -> None:
    with _lock:
        all_ = _read_all()
        all_[cfg.site_id] = asdict(cfg)
        _write_all(all_)


def delete(site_id: str) -> None:
    with _lock:
        all_ = _read_all()
        if site_id in all_:
            del all_[site_id]
            _write_all(all_)


def list_all() -> list[SiteConfig]:
    with _lock:
        rows = _read_all()
    return [SiteConfig(**r) for r in rows.values()]
