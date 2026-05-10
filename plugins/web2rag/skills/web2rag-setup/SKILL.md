---
name: web2rag-setup
description: First-time setup for a freshly-scaffolded web2rag project — verifies docker is installed and running, copies .env.example to .env if missing, and interactively prompts for ANTHROPIC_API_KEY plus the CHAT_MODEL choice (claude-sonnet-4-6 vs claude-haiku-4-5-20251001). Use this skill the first time a user opens a freshly-scaffolded project — phrasings like "set up the project", "first-time setup", "configure my env", "i just ran web2rag-init what's next", "saya baru scaffold project tolong setup envnya" (id). Also trigger when the user says they just generated the project or asks for help configuring it. Do NOT use to scaffold the project (that's web2rag-init) or to start the docker stack (that's web2rag-serve).
allowed-tools: [bash, read, write, edit]
---

# web2rag-setup

Prereq check + interactive `.env` fill for a generated project.

## What this skill does

1. Asserts cwd (or `--project <path>`) contains a generated web2rag project (`docker-compose.yml` + `.env.example` present).
2. Asserts `docker info` exits 0; otherwise prints actionable install instructions and stops.
3. If `.env` does not exist, copies `.env.example` → `.env`.
4. Reads `.env` and prompts ONLY for fields that are still empty/placeholder:
   - `ANTHROPIC_API_KEY` (required — used for chat AND for `--judge anthropic-haiku` audits)
   - `CHAT_MODEL` (offers `claude-sonnet-4-6` and `claude-haiku-4-5-20251001` as choices)
   - `OLLAMA_URL` (optional — only needed if the operator plans to use `--judge ollama:<model>` later)
5. Writes the updated `.env` back atomically (writes to `.env.tmp`, then renames).
6. Prints a checklist: ANTHROPIC_API_KEY present ✓, CHAT_MODEL valid ✓, docker reachable ✓, ready for `/web2rag-serve`.

## Inviolable rules

- Never echo secrets back to the terminal in plain.
- Never commit `.env` — the generated project's `.gitignore` already excludes it; assert that before writing.
