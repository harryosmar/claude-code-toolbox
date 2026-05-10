---
name: sync-folder
description: Sync watched local folders (configured in setup) into the work-intel knowledge base. Picks up files that were dropped in, re-embeds files that changed, and removes embeddings for files that were deleted from the folder — Dropbox semantics. Use this whenever you've added, edited, or removed files in your watched knowledge folders and want the change reflected in briefings/searches without waiting for the next morning-briefing. Trigger phrases - "sync my docs folder", "scan the watched folder", "I dropped a new PDF in there", "ingest folder changes", "what's new in my knowledge folder", "my watched folder", "refresh local knowledge", "rescan local docs", "I deleted a file from the folder, please clean it up". Trigger even if the user just says "sync the folder" without specifying which one.
allowed-tools: [bash, read]
model: sonnet
---

# Sync Folder

Run a delta scan of every configured watched folder. New/changed files get extracted + embedded, deleted files get their chunks removed from ChromaDB. Idempotent — re-running with no changes is a fast no-op.

The user's mental model is "the folder is my source of truth" — anything they put in it should be searchable; anything they take out should disappear from search. Don't second-guess deletions: if a file is gone, its chunks go with it.

## Hard Rules

- Never run before `/work-intel:setup` has configured at least one folder. If `config.local_folders.watches` is empty, print a clear message pointing them at setup and stop.
- Never delete chunks outside the `documents` collection. The script handles this; don't reach into ChromaDB by hand.
- All paths use `$WORK_INTEL_HOME` — never hardcode.
- Files larger than 50MB are skipped with a warning. Don't loop or retry — they're skipped on purpose to keep extraction fast.

## Step 1 — Resolve scope

Optional argument: `--label <name>` to sync just one watched folder. Without it, every configured folder is synced.

Look for the label in the user's prompt (e.g. "sync my research folder" → `--label research`). If the user didn't specify, sync all.

## Step 2 — Run the sync

```bash
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"
python3 "${CLAUDE_SKILL_DIR}/../../scripts/sync_folder.py" ${LABEL_FLAG:-}
```

Where `LABEL_FLAG` is `--label <name>` if you scoped to one folder, otherwise empty.

The script prints one line per folder plus a summary block:

```
folder[work-knowledge]: 2 new, 0 modified, 1 deleted, 47 unchanged
folder[research]: 1 new, 1 modified, 0 deleted, 23 unchanged
SYNC_FOLDER_RESULT: OK
FILES_NEW: 3
FILES_MODIFIED: 1
FILES_DELETED: 1
```

If `SYNC_FOLDER_RESULT: PARTIAL`, individual files failed (see the `warn:` lines in stderr) but the rest succeeded — surface the warnings to the user so they can decide whether to retry.

## Step 3 — Render summary to user

Translate the script output into a friendly confirmation. The point is for the user to see *what changed* without having to read raw machine output.

Format:

```
Synced <N> watched folder(s):

  • <label>: <new> new, <modified> updated, <deleted> removed
    [if any new/modified, list filenames so they can verify]

<if any errors>
Warnings:
  • <warning 1>
  • <warning 2>
```

If everything was zero (nothing changed): say `Already up to date — nothing to sync.` and stop.

## Common failures

- **`config.local_folders.watches is empty`** → user hasn't configured any folder yet. Tell them to run `/work-intel:setup` and add a watched folder, or edit `$WORK_INTEL_HOME/config.json` directly to add a `local_folders.watches` entry.
- **`path does not exist`** → the configured folder was renamed/deleted. Tell the user the path the script tried, and ask whether they want to fix it in config or remove the watch.
- **`extract failed`** for a specific file → the file is corrupt, password-protected, or in an unsupported format. The file is left untracked (it'll retry next sync) but the rest of the run succeeded. Surface the filename so the user can investigate.
