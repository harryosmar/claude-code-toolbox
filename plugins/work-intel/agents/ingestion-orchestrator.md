---
name: ingestion-orchestrator
description: Internal subagent that orchestrates delta ingestion from all configured sources. Reads offsets, calls each source MCP/CLI for new items since the last offset, normalizes to a common schema, and delegates to embedding-agent for storage. Updates offsets only after successful ingestion. Never called directly by users — invoked by morning-briefing. Returns structured INGESTION_RESULT output.
model: sonnet
effort: medium
maxTurns: 30
tools: Bash, Read, Write, mcp__truewatch__truewatch_query, mcp__truewatch__truewatch_ping
---

# Ingestion Orchestrator

Delta-ingest from all configured sources into ChromaDB.

> **Tier B PII redaction is automatic.** `embed_item.py` runs Microsoft Presidio over every item's body before chunking and writes the redacted text + `pii_redacted_count` + `pii_types` to ChromaDB metadata. No action needed in this agent — just hand items to embedding-agent as before.

## Input Contract

Called with:
- `config_path`: path to `config.json`
- `offsets_path`: path to `offsets.json`
- `work_intel_home`: `$WORK_INTEL_HOME`

## Output Contract

```
INGESTION_RESULT: OK | PARTIAL | FAILED
ITEMS_INGESTED: <total count>
FAILED_SOURCES: <comma-separated or empty>
NEW_OFFSETS: <json object with updated offsets>
```

## Hard Rules

- Process all sources in parallel where possible. A single source failure must not block others.
- Offsets are updated in memory during the run. Write to `offsets.json` only after ALL sources complete (atomically). If writing fails, print new offsets to stdout so the caller can recover.
- Never delete ChromaDB data.
- Normalize every item to the common schema before passing to `embed_item.py`.

## Common Item Schema

```json
{
  "id": "<source>:<unique_id>",
  "source": "jira|gitlab|gmail|waha|telegram|truewatch|gdrive|local_folder|manual",
  "type": "ticket|mr|commit|email|message|alert|document",
  "title": "...",
  "body": "...",
  "url": "...",
  "owner": "...",
  "priority": "P0|P1|P2|P3",
  "status": "open|in_progress|resolved|closed",
  "timestamp": "<iso8601>",
  "metadata": {}
}
```

## Per-Source Delta Logic

### Jira
Read offset `jira.updated_after`. Build JQL:
```
project in (<projects>) AND updatedDate > "<offset_datetime>" ORDER BY updated ASC
```
Paginate (maxResults=100) until no more results. Normalize each issue to common schema. Priority mapping: Blocker→P0, Critical→P0, High→P1, Medium→P2, Low→P3.

### GitLab
Read offset `gitlab.since`. Run:
```bash
glab mr list --all --updated-after "<since>" --output json
glab api "groups/<group>/events?after=<since>&per_page=100"
```
Normalize MRs and commits to common schema.

### Gmail
Read offset `gmail.history_id`. Call Gmail History API via MCP to get messages since that historyId. For each message: extract subject + body text. For attachments: call `extract_attachment.py`.

### WhatsApp (WAHA)
Read offset `waha.last_message_timestamp`. For each target in `config.waha.targets`:
Call WAHA MCP `get_messages` with timestamp filter. For each message with media: call WAHA `downloadMedia`. Pass to `extract_attachment.py`.

### Telegram
Read offset `telegram.last_message_id`. For each target in `config.telegram.targets`:
Fetch messages since `offset_id` via Telegram MCP. For documents/media: download and pass to `extract_attachment.py`.

### Truewatch
Read offset `truewatch.last_alert_ts`. Query via MCP:
```
mcp__truewatch__truewatch_query with timestamp filter
```
Normalize alerts: severity critical→P0, warning→P1, info→P2.

### Google Drive
Read offset `gdrive.page_token`. Call `gdrive` MCP `changes.list` with `pageToken`. For changed Docs/Sheets/Slides: export via Drive API (Docs→txt, Sheets→csv, Slides→txt). Pass to `extract_attachment.py`.

### Local folders
Read offset `local_folder.files`. Run:
```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/sync_folder.py"
```
The script handles its own classification (NEW / MODIFIED / DELETED), embedding into the `documents` collection, ChromaDB chunk deletion for removed/modified files, and offset updates. Do **not** call `embed_item.py` for folder items — `sync_folder.py` already does that.

Parse stdout for `SYNC_FOLDER_RESULT: OK | PARTIAL | FAILED`, `FILES_NEW`, `FILES_MODIFIED`, `FILES_DELETED`. Add `FILES_NEW + FILES_MODIFIED` to `ITEMS_INGESTED` (deletions and unchanged files don't count as ingested items). On `PARTIAL`, list affected files in `FAILED_SOURCES` as `local_folder` so the user knows to investigate.

## Embedding Step

After fetching, for each batch of normalized items:
```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/embed_item.py" \
  --items-json '<json_array>' \
  --collection items
```

## Final Offset Update

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/offset_store.py" write \
  --offsets '<new_offsets_json>'
```
