---
name: ingest-document
description: Manually ingest a local file (PDF, DOCX, XLSX, PPTX, Markdown, plain text) into the work-intel knowledge base. Extracts text, chunks, embeds, and stores in ChromaDB. Confirms ingestion with a summary of extracted entities (people, services, dates, action items). Trigger phrases: "ingest this doc", "add this to the knowledge base", "I uploaded a post-mortem", "index this file", "add this PDF", "ingest <filename>", "store this document", "add this to work-intel". Trigger even if the user just drops a file path without a verb.
allowed-tools: [bash, read]
model: sonnet
---

# Ingest Document

Extract, embed, and store a local file in the ChromaDB knowledge base.

The user wants their document's content to be searchable in future briefings and meeting preps. The key outputs are: (1) the file is in ChromaDB, and (2) the user gets a clear confirmation of what was extracted so they know the ingestion worked.

## Hard Rules

- Only ingest files that exist on the local filesystem. Never fetch from URLs.
- Never overwrite existing embeddings — `embed_item.py` uses upsert; ChromaDB deduplicates by document ID.
- If the file is larger than 50MB, warn the user and ask for confirmation before proceeding.
- All paths use `$WORK_INTEL_HOME` — never hardcode.
- Extract entities from the text directly — do not query ChromaDB to find entities you just wrote.

## Supported Types

`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.md`, `.txt`, `.eml`

## Step 1 — Validate file

```bash
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"
FILE="<filepath expanded with $HOME if needed>"
```

- Check file exists: `test -f "$FILE"` — if not, print error and stop.
- Check extension is supported — if not, print error listing supported types and stop.
- Check size: `du -m "$FILE" | cut -f1` — if > 50MB, warn user and ask for confirmation.

## Step 2 — Extract text

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/extract_attachment.py" \
  --file "$FILE" \
  --output-json
```

Output: `{ "text": "...", "metadata": { "file": "...", "pages": N, "mime_type": "...", "pii_redacted_count": N, "pii_types": [...] } }`

If extraction fails or text is empty → print error and stop.

The `text` field is **already PII-redacted** by Tier B (Presidio + xlm-roberta multilingual). Detected PII types appear in `metadata.pii_types`. The original raw text is never stored — only the redacted version reaches ChromaDB.

## Step 3 — Embed and store

Compute a stable document ID from the filename and a short content hash to enable deduplication:

```bash
FILENAME=$(basename "$FILE")
HASH=$(echo "$FILE" | shasum -a 256 | cut -c1-8)
DOC_ID="doc:${FILENAME}:${HASH}"
INGESTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
```

Write extracted text to a temp file (avoids shell arg length limits for large documents):

```bash
TMPFILE=$(mktemp /tmp/work-intel-ingest-XXXXXX.txt)
# write the extracted text to $TMPFILE
python3 "${CLAUDE_SKILL_DIR}/../../scripts/embed_item.py" \
  --collection documents \
  --source manual \
  --id "$DOC_ID" \
  --title "$FILENAME" \
  --text-file "$TMPFILE" \
  --metadata "{\"file\": \"$FILE\", \"ingested_at\": \"$INGESTED_AT\", \"mime_type\": \"<mime_type>\"}"
rm -f "$TMPFILE"
```

If `embed_item.py` doesn't support `--text-file`, fall back to:
```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/embed_item.py" \
  --collection documents \
  --source manual \
  --id "$DOC_ID" \
  --title "$FILENAME" \
  --text "<extracted_text>" \
  --metadata "{\"file\": \"$FILE\", \"ingested_at\": \"$INGESTED_AT\", \"mime_type\": \"<mime_type>\"}"
```

## Step 4 — Summarize ingestion

Extract entities directly from the text you already have — no need to query ChromaDB.

From the extracted text, identify:
- **People**: any names or @lid handles mentioned
- **Services / systems**: service names, repo names, ticket IDs (e.g. SIPGN-1234)
- **Key dates**: deadlines, incident timestamps, meeting dates
- **Action items**: any tasks with an owner or due date

Print confirmation:

```
✅ Ingested: <filename>
   Chunks stored: <n>
   PII redacted: <pii_redacted_count> items (<comma-separated pii_types> or "none")
   Extracted entities:
   - People: <list, or "none found">
   - Services: <list, or "none found">
   - Key dates: <list, or "none found">
   - Action items: <list, or "none found">
```

The PII line surfaces what Tier B detected — useful for the user to spot false positives (e.g. a 16-digit code that wasn't actually a NIK).

The entity summary helps the user verify the extraction was meaningful before trusting the document in future briefings.
