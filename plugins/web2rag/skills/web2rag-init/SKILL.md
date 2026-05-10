---
name: web2rag-init
description: Scaffold a brand-new self-contained website-to-RAG chatbot project as a sibling directory of this plugin. Generates a docker-compose stack (FastAPI + Chroma), the FastAPI server with in-process bilingual BGE-M3 embeddings + 3-layer guard architecture, an embeddable Web Component widget, and a switchable RAGAS/deepeval/deepteam audit suite. Use this skill whenever the user wants to start a new website-to-RAG chatbot project, "turn a website into a chatbot," scaffold a fresh RAG service for a customer, or generate a docker-compose RAG stack from scratch — phrasings like "scaffold a new web2rag project for X," "create a chatbot for docs.acme.com," "make me a RAG service for my-customer," or "buatin project chatbot baru" (id). Trigger even when the user just gives a project name and a URL together. Do NOT use to ingest content (that's web2rag-ingest), start an existing stack (that's web2rag-serve), or scaffold a Go service (that's peruri-go-scaffolder).
allowed-tools: [bash, read, write, edit, glob]
---

# web2rag-init

Generate a new sibling project from `templates/`.

## Inputs

- `<name>` — project directory name (required, e.g. `acme-bot`)
- `--target <dir>` — parent directory under which `<name>/` is created (default: parent of the plugin's own directory)

## What this skill does

1. Resolves the target path: `<target>/<name>/`. Refuses if it already exists (operator must remove explicitly).
2. Copies every file under `templates/` into the target. Files ending in `.tmpl` are rendered through Jinja2 with `{{project_name}}` and `{{plugin_version}}` substituted. Verbatim files (Dockerfile, .py, .js, ...) are copied unchanged.
3. Initialises `data/` and `reports/` as empty directories.
4. Prints a "next steps" summary instructing the user to `cd <target>/<name>` and run `/web2rag-setup`.

The script lives at `scripts/init.py` (added in Step 1 of the build).

## Inviolable rules

- Do not write inside the plugin repo when scaffolding — only into the target.
- The plugin's own `CLAUDE.md` is NEVER copied; only `templates/CLAUDE.md.tmpl` is rendered into the generated project.
- If the target exists, fail loudly. Never overwrite without explicit `--force` (and even then, refuse if a `.env` would be clobbered).
