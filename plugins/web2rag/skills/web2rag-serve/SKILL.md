---
name: web2rag-serve
description: Start a generated web2rag project's docker-compose stack — runs `docker compose up -d` and waits for /health to return 200 (model load is ~30-60s on first boot because BGE-M3 + Prompt-Guard-2 + Detoxify + Presidio load into memory). Use whenever the user wants to start, spin up, or run a web2rag chatbot project — phrasings like "serve the project", "start the chatbot service", "spin up the bot", "docker compose up", "jalankan service", "nyalain web2rag stack." Also trigger when the user says they want to "test the bot" or "open the widget" and the stack is not yet running. Do NOT use to stop the stack (that's web2rag-stop), to scaffold a project (that's web2rag-init), or to ingest content (that's web2rag-ingest).
allowed-tools: [bash, read]
---

# web2rag-serve

Start the generated project's stack.

## What this skill does

1. Asserts cwd (or `--project <path>`) contains a generated web2rag project (`docker-compose.yml` present).
2. Asserts `.env` exists and `ANTHROPIC_API_KEY` is non-empty (else points to `/web2rag-setup`).
3. Runs `docker compose up -d`.
4. Polls `curl -fs http://localhost:8787/health` once a second up to 90s. On 200 response, prints the JSON body (which includes model load times + memory footprint) and exits.
5. On timeout, runs `docker compose logs --tail 50 api` and surfaces the last lines.

## Inviolable rules

- Never run `docker compose up` from a non-project directory.
- Don't hide failures — if the health check fails, surface the docker logs verbatim.
