---
name: restore
description: Restore work-intel data from a backup tarball on a new or reset host. Extracts the tarball, downloads the pinned embedding model, verifies ChromaDB integrity with a test query, detects which sources need re-authentication, and prints a readiness report. Trigger phrases: "restore work-intel", "restore from backup", "migrate work-intel to this host", "import work-intel backup".
allowed-tools: [bash, read, write]
model: sonnet
---

# Restore

Restore work-intel from a backup tarball and verify readiness on this host.

## Hard Rules

- Never overwrite an existing `$WORK_INTEL_HOME` without user confirmation. Ask first.
- The embedding model in the backup's `config.json` MUST match what is used going forward — warn loudly if it differs from any locally cached model.
- Never re-init any source that already has a valid offset after restore — the delta sync will continue from the saved offset.
- Print a clear readiness report at the end: ✅ ready / ⚠️ needs re-auth / ❌ failed.

## Input

Path to a backup tarball, e.g.:
```
/work-intel:restore ~/Downloads/work-intel-backup-20260506-090000.tar.gz
```

## Step 1 — Validate tarball

```bash
BACKUP_FILE="<argument>"
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"
```

Check the tarball exists. Print its SHA256 checksum for verification.

If `$WORK_INTEL_HOME` already contains data → ask user: "Overwrite existing data? (yes/no)". Stop if no.

## Step 2 — Extract

```bash
mkdir -p "$WORK_INTEL_HOME"
tar -xzf "$BACKUP_FILE" -C "$WORK_INTEL_HOME"
```

Confirm extracted files: `config.json`, `offsets.json`, `chroma/`. Optional directories that may also be present: `ragas-reports/`, `security-audit/` (newer backups). Both are tolerated when missing — older backups predate `security-audit/`.

## Step 3 — Verify and download embedding model

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/migrate.py" \
  --work-intel-home "$WORK_INTEL_HOME"
```

`migrate.py`:
1. Reads `config.embedding.model` from `config.json`
2. Downloads the model via `sentence-transformers` if not already cached
3. Runs a test embed to confirm model works
4. Runs `chroma_init.py --verify` to check collections and item count
5. Runs a sample semantic query (`query_chroma.py --q "test" --top_k 1`) to confirm vectors work
6. Checks which source credentials are present vs missing

## Step 4 — Print readiness report

```
✅ Restore complete — work-intel is ready

ChromaDB:
  items collection:     <N> chunks  ✅
  documents collection: <N> chunks  ✅

Embedding model: jinaai/jina-embeddings-v5-text-small  ✅
Offsets restored: jira, gitlab, gmail, waha, telegram, truewatch, gdrive

Credentials status:
  Jira:         ✅ (~/.claude/secrets/jira.env found)
  Gmail:        ⚠️  Re-auth needed (OAuth token not transferred)
  Google Drive: ⚠️  Re-auth needed (OAuth token not transferred)
  WAHA:         ✅ (API key in config, WAHA Docker must be running)
  Telegram:     ✅ (~/.claude/secrets/telegram.env found)
  Truewatch:    ✅ (MCP server configured)

Next steps:
  1. Re-authenticate Gmail: run /work-intel:setup and select Gmail
  2. Re-authenticate Google Drive: run /work-intel:setup and select Google Drive
  3. Run /work-intel:morning-briefing to resume from saved offsets
```
