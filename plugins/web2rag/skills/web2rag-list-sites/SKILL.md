---
name: web2rag-list-sites
description: List every website that has been ingested into a generated web2rag project's vector store, with page counts, chunk counts, and last-updated timestamps. Use whenever the user wants to see what's in their chatbot's knowledge base — phrasings like "list sites", "what's in the bot", "show me ingested sites", "what does the bot know about", "apa aja yang udah diingest" (id), "site mana aja yang aktif". Also trigger when the user wants to verify an ingest succeeded ("did docs.acme.com get ingested?"). Do NOT use to remove a site (that's web2rag-remove-site) or to update a site (that's web2rag-update).
allowed-tools: [bash, read]
---

# web2rag-list-sites

Print a table of ingested sites.

## What this skill does

1. GETs `/sites` from the running api.
2. Prints a table with columns `site_id, page_count, chunk_count, last_updated`, ordered by `last_updated DESC`.
3. If no sites are present, prints "no sites ingested yet — run /web2rag-ingest <url>".

## Output shape

```
site_id              | pages | chunks | last_updated
docs.acme.com        |   142 |   1108 | 2026-05-08 09:12:33+07
docs.crawl4ai.com    |    20 |    156 | 2026-05-07 14:22:01+07
```
