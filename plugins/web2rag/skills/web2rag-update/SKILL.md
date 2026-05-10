---
name: web2rag-update
description: Refresh an already-ingested site by re-crawling it and re-embedding only pages whose content changed (delta re-ingest via sitemap lastmod + Last-Modified header + SHA-256 content hash). Cheap to run on a schedule. Use whenever the user wants to refresh, sync, or update a site that's already in the knowledge base — phrasings like "update docs.acme.com", "refresh the bot's knowledge", "pull latest from docs.acme.com", "sync docs.acme.com", "update knowledge base", "re-ingest the site". Also trigger on phrasings like "the docs changed, update the bot" or short forms like "sync acme". Do NOT use for first-time ingestion (that's web2rag-ingest) or to remove a site (that's web2rag-remove-site).
allowed-tools: [bash, read]
---

# web2rag-update

Delta re-crawl an already-ingested site.

## Inputs

- `<site_id>` — site identifier as listed by `/web2rag-list-sites` (e.g. `docs.acme.com`)
- `--project <path>` — target project (default cwd)

## What this skill does

1. Looks up the site's last `ingestion_batch_id` and original start URL from chroma.
2. Re-crawls the site under the same politeness settings as the previous run.
3. For each fetched page:
   - If sitemap `<lastmod>` is older than our last visit → skip without fetching.
   - If `Last-Modified` / `ETag` matches → skip.
   - Else fetch + hash. If hash matches what's stored → skip.
   - Else: re-chunk, re-embed, upsert; delete superseded chunks (matched by `source_url`).
4. Rotates `ingestion_batch_id` for any chunk that was upserted.
5. Prints a summary: `pages_unchanged`, `pages_changed`, `chunks_re_embedded`, `chunks_deleted`.

## Inviolable rules

- Same zero-cost contract as `/web2rag-ingest`.
- Never mass-delete and re-ingest — only changed pages move; everything else stays put.
