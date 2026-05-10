---
name: web2rag-ingest
description: Crawl a website and ingest it into a generated web2rag project's vector store — runs scraper → guard_ingest → chunker → BGE-M3 embedder → chroma writer. **Ingest is a hard zero-cost operation** (no Anthropic / OpenAI / Voyage / Cohere calls). Supports per-call politeness overrides for sites the operator owns or is authorised to crawl: `--force` (ignore robots.txt), `--rate N/<window>` (e.g. `5/10s`), `--concurrency N`, `--user-agent "..."`. Use whenever the user wants to add a website's content to their chatbot's knowledge base — phrasings like "ingest <url>", "crawl this site for the bot", "add docs.acme.com to the knowledge base", "embed this site", "index these pages", "tambahin website ini ke bot" (id), "crawl docs.acme.com but ignore robots.txt — it's our own site" (→ adds `--force`). Also trigger on bare URLs paired with intent verbs ("index this", "feed this to the bot"). Do NOT use to update an already-ingested site (that's web2rag-update) or to remove a site (that's web2rag-remove-site).
allowed-tools: [bash, read]
---

# web2rag-ingest

Crawl a website and write its embeddings into the generated project's chroma store.

## Inputs

- `<url>` — start URL (required)
- `--max-pages N` (default 100)
- `--depth N` (default 2 hops from start URL)
- `--force` — ignore robots.txt and `<meta robots noindex>`
- `--rate N/<window>` — e.g. `30/60s` (default `10/60s`, env `CRAWL_RATE_LIMIT`)
- `--concurrency N` — max parallel fetches (default 2, env `CRAWL_CONCURRENCY`)
- `--user-agent "..."` — override UA (default modern Chrome UA, env `CRAWL_USER_AGENT`)
- `--project <path>` — target project (default cwd)

## What this skill does

1. POSTs to the running api's `/ingest` endpoint with the parsed flags.
2. Streams progress lines back to the operator (pages crawled, chunks embedded, guard hits).
3. On completion, prints a summary: `pages_crawled`, `chunks_embedded`, `guard_ingest_hits` (per severity), wall-clock time.
4. Refuses with an actionable error if the api is not reachable (points the user at `/web2rag-serve`).

## Inviolable rules

- **Zero-cost contract.** This skill MUST NOT make any outbound paid API calls. The api implementation enforces this by routing only through the in-process embedder.
- Politeness overrides are logged into `reports/audit_<ts>.json` with the operator identity so intent stays traceable.
- Pages flagged by `guard_ingest` with `severity=high` are dropped (not chunked, not embedded).
