---
name: setup
description: One-time initialization for work-intel. Run this first before using any other work-intel skill. Interactively collects which WhatsApp groups/communities/private contacts, Telegram channels/groups/persons, Jira projects, GitLab groups, Gmail labels, and Google Drive folders to monitor. Saves config, initializes ChromaDB, performs a full historical fetch from every source, embeds all items, and writes initial offsets. Also auto-triggered by any skill that detects a missing offset for a source. Trigger phrases: "setup work-intel", "initialize work-intel", "first time setup", "configure work-intel".
allowed-tools: [bash, read, write]
model: sonnet
---

# work-intel Setup

One-time initialization. Collects source configuration, fetches full history, and prepares ChromaDB.

## Hard Rules

- Never re-run init for a source that already has an offset in `offsets.json` — skip it and print "already initialized".
- If any source fails during init, skip it and continue with the others. Print a ⚠️ warning. The user can re-run `/work-intel:setup` to retry failed sources.
- Never delete existing ChromaDB data.
- Store all data under `$WORK_INTEL_HOME` (default `~/.work-intel/`). Never hardcode paths.

## Step 1 — Prepare environment

```bash
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"
mkdir -p "$WORK_INTEL_HOME/chroma" "$WORK_INTEL_HOME/ragas-reports"
python3 "${CLAUDE_SKILL_DIR}/../../scripts/chroma_init.py"
```

If `chroma_init.py` fails (missing deps), print the install command from `references/mcp-setup.md` and stop.

## Step 2 — Load or create config

Check if `$WORK_INTEL_HOME/config.json` exists.

**If it exists:** load it, show a summary of already-configured sources, and ask the user which sources (if any) they want to reconfigure or add. Skip sources that already have offsets in `offsets.json`.

**If it does not exist:** interactively ask the user for each source below. Accept answers as comma-separated lists or one item at a time.

### WhatsApp (WAHA)
Ask:
- WAHA groups to monitor (name or ID, e.g. "Engineering Team", "Company Community")
- WAHA communities to monitor
- Private contacts (persons) to monitor by name

Save as:
```json
"waha": {
  "session": "default",
  "targets": [
    { "type": "group",     "id": "<resolved_id>", "name": "<name>" },
    { "type": "community", "id": "<resolved_id>", "name": "<name>" },
    { "type": "private",   "id": "<resolved_id>", "name": "<name>" }
  ]
}
```

To resolve IDs, call the WAHA MCP tool to list all chats and match by name.

### Telegram
Ask: channels, groups, and private contacts to monitor (names or @handles).
Save as:
```json
"telegram": {
  "targets": [
    { "type": "channel", "id": "<id>", "name": "<name>" },
    { "type": "group",   "id": "<id>", "name": "<name>" },
    { "type": "private", "id": "<id>", "name": "<name>" }
  ]
}
```

### Jira
Ask: project keys to monitor (e.g. `ENG, INFRA, PLATFORM`). Ask: how many days of history to fetch (default 90).

### GitLab
Ask: group/project paths to monitor (e.g. `engineering/backend`). Ask: how many days of history (default 30).

### Gmail
Ask: labels to monitor (default `INBOX, STARRED`). Ask: how many days of history (default 90).

### Google Drive
Ask: folders to sync (default `shared-with-me, my-drive`). Can also specify folder names or IDs.

### Watched local folders
Ask: do you want a "drop-zone" folder for reference docs (PDF/DOCX/XLSX/PPTX/Markdown) that auto-flow into the knowledge base?

If yes, collect one or more `{path, label}` pairs. The label is a short tag (e.g. `work-knowledge`, `research`, `post-mortems`) used to scope future syncs and tag stored chunks. Default suggestion: `~/work-intel-knowledge` with label `work-knowledge`.

For each folder, also confirm:
- Extensions to pick up (default: `.pdf .docx .xlsx .pptx .md .txt`)
- Recursive descent into subfolders (default: yes)

Save as:
```json
"local_folders": {
  "watches": [
    {
      "path": "/Users/<you>/work-intel-knowledge",
      "label": "work-knowledge",
      "extensions": [".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt"],
      "recursive": true
    }
  ]
}
```

If the folder doesn't exist yet, create it (`mkdir -p <path>`) so the user has somewhere to drop files immediately after setup.

Sync semantics (worth telling the user once during setup):
- Files added → ingested
- Files modified → re-embedded (stale chunks removed first)
- Files deleted → chunks removed from the knowledge base (Dropbox-style)

The user can run `/work-intel:sync-folder` any time to pick up changes; it also runs automatically during `/work-intel:morning-briefing`.

### Truewatch
No config needed — ask: how many hours of alert history to fetch on init (default 72).

### RAGAS judge + security-audit (Tier C batch judge)
Both `/work-intel:evaluate-rag` and `/work-intel:security-audit` use the same judge. Ask:
- `provider`: `anthropic` (Haiku, subscription) or `ollama` (free, can be a remote host).
- If `anthropic`: confirm `ANTHROPIC_API_KEY` is set in env.
- If `ollama`: ask for `ollama_base_url` (default `http://localhost:11434`; can be a remote machine like `http://192.0.2.10:11434`). Run `curl <base_url>/api/tags` to confirm reachability — fail loud if not.

Save under top-level `judge` (the legacy `ragas` block is also kept for backward compat with older configs).

### Tier B inline ingest guards (PII redaction)
The plugin runs Microsoft Presidio over every ingested item to redact PII (emails, phones, names, Indonesian NIK / NPWP / BPJS) into `[REDACTED:TYPE]` markers before they reach ChromaDB. Multilingual: English + Bahasa Indonesia.

Default backend is spaCy (~11 MB model, ~30 ms/item) — fine for most users. Optional `transformers` backend uses xlm-roberta (~1.1 GB, ~250 ms/item) for higher Bahasa recall.

Disk-space check: warn if `df -h $HOME` shows <2 GB free before downloading models.

### Tier A inline guards (chatbot input + output)
`guard-input-agent` and `guard-output-agent` run as Claude Code subagents — no API key, no extra cost. Ask for the model:
- `sonnet` (default — more accurate for security-critical decisions)
- `haiku` (faster, lighter)

Save final `config.json` including:
```json
{
  "embedding": { "model": "jinaai/jina-embeddings-v5-text-small", "dim": 1024 },
  "judge": {
    "provider": "<anthropic|ollama>",
    "anthropic_model": "claude-haiku-4-5-20251001",
    "ollama_model": "llama3.2",
    "ollama_base_url": "<remote URL or http://localhost:11434>"
  },
  "guards": {
    "ingest": { "enabled": true, "pii_classifier": { "backend": "spacy", "languages": ["en", "id"] } },
    "query": { "enabled": true, "subagent_model": "sonnet" },
    "output": { "enabled": true, "subagent_model": "sonnet" }
  },
  "security_audit": { "sample_size": 200 }
}
```

## Step 2.5 — Install Tier B + Tier C Python deps

Tier B (Presidio) is mandatory; Tier C (deepteam) is optional and only needed for `/work-intel:security-audit`:

```bash
# Tier B (required)
pip install presidio-analyzer presidio-anonymizer
python -m spacy download en_core_web_sm
python -m spacy download xx_ent_wiki_sm

# Tier C (optional — install only if user wants security-audit)
pip install deepteam
```

After install, run a quick judge health check (only if `provider: anthropic` or remote `ollama_base_url` is set):

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/_llm_judge.py" --health-check
```

If the health check fails, print the error verbatim and tell the user to fix `judge.provider` / `judge.ollama_base_url` / `ANTHROPIC_API_KEY` before running `/work-intel:evaluate-rag` or `/work-intel:security-audit`. Other skills (morning-briefing, incident-context, pre-meeting-intel) are NOT blocked — they use Tier A subagents which don't need the judge.

## Step 3 — Init each source

For each source not yet in `offsets.json`, run:

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/init_source.py" --source <name> --config "$WORK_INTEL_HOME/config.json"
```

`init_source.py` handles: paginated fetch from beginning → `unstructured` extraction for attachments → `embed_item.py` for each item → writes offset on completion.

Print progress per source: `✅ jira: 847 items ingested` or `⚠️ telegram: failed — <reason>`.

## Step 4 — Verify

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/chroma_init.py" --verify
```

Print total item count per collection. Print: `✅ work-intel is ready. Run /work-intel:morning-briefing to start.`
