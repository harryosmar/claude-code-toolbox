---
name: backup
description: Create a portable backup tarball of all work-intel data (ChromaDB vectors, config, offsets, RAGAS testset and reports, security-audit reports). The tarball can be copied to a new host and restored with /work-intel:restore. OAuth tokens are excluded (device-bound) and raw pre-redaction text (security-audit/_raw/) is excluded too — raw audit text shouldn't travel between hosts. Trigger phrases: "backup work-intel", "export work-intel data", "create a backup", "pack work-intel for migration".
allowed-tools: [bash, read]
model: sonnet
---

# Backup

Create a portable tarball of all work-intel data.

## Hard Rules

- Never include OAuth tokens or API keys in the tarball (they are device-bound and would expose credentials).
- Always write the backup to the current directory unless the user specifies a path.
- Print the tarball size and SHA256 checksum after creation.

## Step 1 — Prepare

```bash
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"
BACKUP_NAME="work-intel-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
BACKUP_PATH="${1:-$(pwd)}/$BACKUP_NAME"
```

## Step 2 — Create tarball (exclude OAuth tokens)

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/backup.py" \
  --source "$WORK_INTEL_HOME" \
  --output "$BACKUP_PATH"
```

`backup.py` tarballs:
- `chroma/` — ChromaDB vector data
- `config.json` — source config + model pin (no secrets)
- `offsets.json` — delta cursors
- `ragas-testset.json` — evaluation testset
- `ragas-reports/` — RAGAS evaluation history
- `security-audit/` — `/work-intel:security-audit` reports (excludes `_raw/` subdir; raw pre-redaction text is never packed)

Excludes: `*.token`, `*.oauth`, `credentials.json`, any file matching `*secret*` or `*key*`, plus the `_raw/` subdirectory anywhere in the tree.

## Step 3 — Print summary

```bash
CHECKSUM=$(shasum -a 256 "$BACKUP_PATH" | awk '{print $1}')
SIZE=$(du -sh "$BACKUP_PATH" | awk '{print $1}')
echo "✅ Backup created: $BACKUP_PATH"
echo "   Size: $SIZE"
echo "   SHA256: $CHECKSUM"
echo ""
echo "To restore on a new host:"
echo "  /work-intel:restore $BACKUP_PATH"
echo ""
echo "⚠️  Note: OAuth tokens (Gmail, Google Drive) are NOT included."
echo "   You will need to re-authenticate those sources after restore."
```
