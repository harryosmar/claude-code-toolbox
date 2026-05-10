---
name: web2rag-stop
description: Stop a generated web2rag project's docker-compose stack — runs `docker compose down` and confirms the api + chroma containers exited. Use whenever the user wants to stop, halt, kill, or tear down a running web2rag chatbot project — phrasings like "stop the chatbot", "stop the service", "shut down web2rag", "docker compose down", "matikan stack", "matiin servernya" (id). Also trigger when the user says "i'm done for now" or "free up the ports" while a web2rag stack is running. Do NOT use to remove ingested data (that's web2rag-remove-site) or to scaffold projects.
allowed-tools: [bash, read]
---

# web2rag-stop

Stop the generated project's stack.

## What this skill does

1. Asserts cwd (or `--project <path>`) contains a generated web2rag project.
2. Runs `docker compose down`.
3. Asserts `docker compose ps -q` returns empty.
4. Prints "stopped" + the path that was stopped.

Persistent volumes (`./data/chroma`) are NOT removed by this skill. To purge ingested data, the user runs `/web2rag-remove-site` per site or `docker compose down -v` manually.
