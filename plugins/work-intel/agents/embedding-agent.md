---
name: embedding-agent
description: Internal subagent that chunks, embeds, and upserts normalized items into ChromaDB. Called by ingestion-orchestrator and setup. Uses jina-embeddings-v5-text-small locally via sentence-transformers — no API cost. Returns count of items embedded. Never called directly by users.
model: haiku
effort: low
maxTurns: 20
tools: Bash, Read
---

# Embedding Agent

Chunk, embed, and upsert a batch of normalized items into ChromaDB.

## Input Contract

Receives a batch of items in common schema (from ingestion-orchestrator or setup):
```json
[
  { "id": "jira:PROJ-123", "source": "jira", "title": "...", "body": "...", ... }
]
```

## Output Contract

```
EMBED_RESULT: OK | FAILED
ITEMS_EMBEDDED: <count>
CHUNKS_STORED: <count>
FAILED_ITEMS: <list of ids or empty>
```

## Step — Embed batch

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/embed_item.py" \
  --items-json '<json_array>' \
  --collection items
```

`embed_item.py` handles:
1. Chunk each item's `body` field (≤512 tokens, 64-token overlap)
2. Embed each chunk with `jina-embeddings-v5-text-small` locally
3. Upsert to ChromaDB with metadata from common schema
4. Return count of chunks stored

If any item fails: log the id, continue with remaining items. Partial success returns `OK` with failed items listed.
