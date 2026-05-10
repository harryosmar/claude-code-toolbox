# claude-code-toolbox

Harry's personal Claude Code plugin marketplace. Three plugins today, more land alongside.

```
claude-code-toolbox/
├── .claude-plugin/
│   └── marketplace.json
├── .claude/
│   └── commands/
│       └── release-claude-code-toolbox.md       ← /release-claude-code-toolbox <name> <bump> "<subject>"
└── plugins/
    ├── web2rag/
    ├── mac-housekeeping/
    ├── work-intel/
    └── *-workspace/                    ← local-only eval data, gitignored
```

## Plugins

| Plugin | Version | What it ships |
|---|---|---|
| [`web2rag`](./plugins/web2rag/) | 0.1.0 | Scaffolder that turns any website into a self-hosted RAG chatbot. Generates a sibling docker-compose project (FastAPI + Chroma) with in-process bilingual BGE-M3 embeddings, 3-layer guard architecture (`guard_ingest` / `guard_query` / `guard_output`), Claude-powered chat via native Citations API (Sonnet 4.6 / Haiku 4.5 selectable), embeddable Web Component widget with thumbs-up/down feedback, and DeepEval (correctness + capability) + DeepTeam (red-team) audit suite with switchable Anthropic Haiku / native-Ollama / OpenAI-compatible / inline-URL judges. Skills include `/web2rag-init`, `/web2rag-ingest` (zero-cost crawl), `/web2rag-chat` (paid SSE from TUI), `/web2rag-chat-test` (cost-free preview via in-session subagent), `/web2rag-audit`. |
| [`mac-housekeeping`](./plugins/mac-housekeeping/) | 0.1.0 | Mac-only disk-space toolkit. `/disk-audit` produces a read-only ranked report of biggest space consumers across `~/Library/Caches`, `Containers`, `Group Containers`, `Application Support`, `~/go`, scattered `node_modules`, etc., classified by reversibility class (`safe-cache` / `project-rebuild` / `system-app` / `app-manual` / `user-data`). `/disk-cleanup` drives a tiered, per-action consent-gated reclaim flow with mac-specific gotcha handling — Docker.raw sparse-file behaviour (`prune` reports reclaim but file doesn't shrink), `~/Library/Group Containers/group.net.whatsapp.*` is live app state not cache, MCP server `node_modules` powers active session tools, auto-mode classifier deletion blocks → fall-back to handing the user the exact command to paste. |
| [`work-intel`](./plugins/work-intel/) | 0.3.0 | Context Intelligence plugin — delta-ingests Jira, GitLab, Gmail, WhatsApp (WAHA), Telegram, Google Drive (Docs/Sheets/Slides), Truewatch APM, and watched local folders (Dropbox-style drop-zone for PDF/DOCX/XLSX/PPTX/Markdown reference docs — new files auto-embed, modified files re-embed, removed files lose their chunks) into a local ChromaDB vector knowledge base. Provides daily briefings, incident root-cause context, meeting prep, document ingestion, RAG quality evaluation via RAGAS, and portable backup/restore. Role-agnostic. Zero external API cost (local embeddings + Ollama judge by default). |

Each plugin under `plugins/` has its own `plugin.json`, `README.md` (where applicable), version, and release lifecycle. Bumping `web2rag` doesn't touch the others.

## Install

Register the marketplace once:

```
# clone first, then point Claude Code at the local checkout:
git clone https://github.com/harryosmar/claude-code-toolbox.git ~/projects/claude-code-toolbox
/plugin marketplace add ~/projects/claude-code-toolbox
```

Then install whichever plugins you want:

```
/plugin install web2rag@claude-code-toolbox-marketplace
/plugin install mac-housekeeping@claude-code-toolbox-marketplace
/plugin install work-intel@claude-code-toolbox-marketplace
```

After install, reload your Claude Code session. Each plugin's slash commands become available — see the per-plugin README for the surface.

## Release flow

This repo ships a project-local slash command for marketplace maintainers: `/release-claude-code-toolbox`. It bumps a single plugin's version, validates the marketplace, commits, pushes, refreshes the registry, and updates the local install — all with explicit confirmation gates.

```
/release-claude-code-toolbox <plugin-name> <patch|minor|major> "<commit subject>"
```

Example:

```
/release-claude-code-toolbox web2rag patch "fix retrieve top_k default override"
```

The command lives at `.claude/commands/release-claude-code-toolbox.md` — only visible when Claude Code is launched from this repo, never shipped to plugin installers. See its body for the full step-by-step (validation gates, revert-on-decline behaviour, no-amend / no-force-push rules).

## Add a new plugin

1. Drop the plugin tree under `plugins/<name>/` with a `.claude-plugin/plugin.json` (must include `name`, `description`, `version`).
2. Append an entry to `.claude-plugin/marketplace.json` under `plugins[]`.
3. Add a row to the `## Plugins` table above (so `/release-claude-code-toolbox` can update its version cell on later releases).
4. `/plugin marketplace update claude-code-toolbox-marketplace` to refresh the registry.
5. `/plugin install <name>@claude-code-toolbox-marketplace`.

## Local-only artifacts (gitignored)

The marketplace's `.gitignore` excludes:
- `plugins/*-workspace/` — sibling eval workspaces produced by `skill-creator` runs
- `plugins/*/skills/*-workspace/` — legacy nested-workspace pattern (kept for plugins not yet migrated)
- `plugins/*/.peruri-code-check.md` — output of `/peruri-code-analyzer:code-check`
- Standard noise: `__pycache__/`, `*.py[cod]`, `.DS_Store`, `.idea/`, `.vscode/`, `.env`, `.env.local`

These exist locally during development but never get distributed.
