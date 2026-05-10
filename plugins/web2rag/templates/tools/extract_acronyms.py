"""Backfill auto-acronyms from an already-ingested corpus.

Use this when:
  - You upgraded a project that pre-dates the auto-acronym feature.
  - You manually edited a per-site table and want to rebuild from scratch.
  - You want to seed acronyms from imported data (chroma was populated by
    a non-/ingest path).

What it does:
  1. Connects to the same chroma collection the api uses (env: CHROMA_HOST,
     CHROMA_PORT, default localhost:8000).
  2. For each site_id (or just one if --site-id passed), pulls every chunk's
     text and runs server.ingestion.acronyms.extract_acronyms over the
     concatenation.
  3. Writes the result to data/acronyms/<site_id>.json (atomic).

Cost: zero — pure regex over already-stored text.

Run from inside the api container:
    docker exec <api-container> python -m tools.extract_acronyms
    docker exec <api-container> python -m tools.extract_acronyms --site-id www.example.com
    docker exec <api-container> python -m tools.extract_acronyms --print-only
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict

from server.ingestion.acronyms import extract_acronyms, merge_tables
from server.store import acronyms as acronym_store
from server.store.chroma import collection

log = logging.getLogger("extract_acronyms")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _per_site_chunks() -> dict[str, list[str]]:
    """Group every chunk's text by its site_id metadata."""
    data = collection().get(include=["documents", "metadatas"])
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    grouped: dict[str, list[str]] = defaultdict(list)
    for doc, meta in zip(docs, metas):
        if not doc:
            continue
        sid_raw = (meta or {}).get("site_id")
        if not sid_raw:
            continue
        # chroma's metadata API returns a value-union type; coerce to str
        # since site_id is always a domain string in our writer path.
        sid = str(sid_raw)
        grouped[sid].append(doc)
    return grouped


def main() -> int:
    p = argparse.ArgumentParser(description="Backfill auto-acronyms from chroma")
    p.add_argument("--site-id", help="Only process this site_id (default: every site)")
    p.add_argument("--print-only", action="store_true",
                   help="Print extracted acronyms but don't write to data/acronyms/")
    p.add_argument("--reset", action="store_true",
                   help="Replace existing per-site tables instead of merging "
                        "(useful when correcting misextractions)")
    args = p.parse_args()

    grouped = _per_site_chunks()
    if not grouped:
        log.warning("no chunks found in chroma; nothing to do")
        return 0

    sites = [args.site_id] if args.site_id else sorted(grouped.keys())
    if args.site_id and args.site_id not in grouped:
        log.error("site %r not present in chroma; available: %s", args.site_id, sorted(grouped.keys()))
        return 2

    grand_total = 0
    for sid in sites:
        chunks = grouped.get(sid, [])
        if not chunks:
            continue
        # Run the extractor over each chunk individually, then merge — safer
        # than a single concatenation because regex backtracking is bounded
        # and the merge step still does first-write-wins dedupe.
        per_chunk = [extract_acronyms(c) for c in chunks]
        new_table = merge_tables(*per_chunk)

        if args.print_only:
            log.info("[%s] %d acronyms extracted (print-only):", sid, len(new_table))
            for k, v in sorted(new_table.items()):
                log.info("    %-10s → %s", k, v)
            grand_total += len(new_table)
            continue

        if args.reset:
            acronym_store.save(sid, new_table)
            final = new_table
        else:
            final = acronym_store.merge(sid, new_table)

        log.info(
            "[%s] %d chunks scanned, %d acronyms in table (%d new this run)",
            sid, len(chunks), len(final), len(new_table),
        )
        grand_total += len(final)

    log.info("done. total acronyms across %d site(s): %d", len(sites), grand_total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
