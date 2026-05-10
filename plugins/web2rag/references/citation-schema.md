# Citation metadata schema

Single source of truth for the metadata stored on every chunk in `web_corpus` (the generated project's chroma collection). Read this before touching anything in `templates/server/store/chroma.py`, `templates/server/ingestion/`, or `templates/server/retrieval/prompt.py`.

## Per-chunk metadata fields

| Field | Type | Source | Used by |
|---|---|---|---|
| `source_url` | string | scraper | citation rendering, dedup, deletion |
| `site_id` | string | derived from start URL host | site-scoped filtering, cross-site retrieval, deletion |
| `section_title` | string | scraper (HTML `<h1>`/`<h2>` walk) | citation hover preview |
| `chunk_index` | int | chunker (within `source_url`) | citation hover preview |
| `chunk_hash` | string (sha256) | chunker | upsert dedup, delta detection |
| `ingestion_batch_id` | string (uuid) | pipeline | rotated per ingest call; lets `/web2rag-update` purge stale chunks |
| `last_updated` | string (ISO 8601) | pipeline | sort/list, "this answer is N days old" rendering |
| `page_title` | string | scraper (`<title>`) | citation hover card title |
| `anchor_text` | string \| null | scraper (closest preceding `id=...` element) | deep-link citations |
| `language` | string (`en` \| `id` \| null) | langdetect on the chunk text | optional retrieval filter |

## Lifecycle

1. **Ingest** — pipeline computes `chunk_hash`, attaches all metadata, calls `chroma.upsert(ids=[hash], embeddings=..., metadatas=...)`.
2. **Query** — retrieval surfaces top-N chunks; the prompt assembler hands them to Claude as `DocumentBlockParam` (one per chunk) with `citations.enabled=true`. Claude's response includes `TextBlock.citations[]` with `document_index` (which we map back to `source_url` via the same ordering).
3. **Update** — delta logic upserts changed chunks (same `source_url`, new `chunk_hash`) and deletes orphaned ones via `chroma.delete(where={source_url: ..., ingestion_batch_id: {"$ne": new_batch}})`.
4. **Remove** — `chroma.delete(where={site_id: X})` purges everything for one site.

## Why ONE collection (not one per site)

- Operator can query across sites later (e.g. "search all customer docs") without merging collections.
- Chroma's `where` clause cleanly supports per-site filtering AND per-batch cleanup.
- Avoids the NVIDIA-RAG-blueprint reranker context-length cap (5 collections max for multi-collection joins).

## Citation event in the SSE stream

The widget receives:

```json
{
  "type": "citations",
  "citations": [
    {
      "index": 0,
      "url": "https://docs.acme.com/sso#setup",
      "title": "SSO setup",
      "section_title": "Configuring SAML",
      "snippet": "...the SP-initiated flow requires the IdP entity ID...",
      "char_start": 150,
      "char_end": 200
    }
  ]
}
```

`index` is stable across `citations` and any inline `[N]` markers in the streamed answer text.
