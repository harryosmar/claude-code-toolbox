# web2rag

Claude Code scaffolder plugin that turns any website into a self-hosted RAG chatbot with an embeddable widget.

The plugin generates a fully independent sibling project containing:

- **docker-compose stack** — FastAPI + Chroma (no Ollama in the stack)
- **In-process bilingual embeddings** — BGE-M3 (EN + ID) + bge-reranker-v2-m3, baked into the API image
- **3-layer guard architecture** — `guard_ingest`, `guard_query`, `guard_output` (free by default)
- **Claude-powered chat** with native Anthropic Citations API (model selectable: Sonnet 4.6 or Haiku 4.5)
- **Four-axis eval** — DeepEval for correctness (Faithfulness / AnswerRelevancy / ContextualPrecision / ContextualRecall) + capability (GEval over helpfulness, task completion, scope adherence) + production thumbs-up/down feedback widget. DeepTeam for security/red-team (jailbreak, prompt injection, bias, toxicity, prompt leakage). Judge swappable between Haiku and a remote Ollama URL.
- **Embeddable widget** — vanilla-JS Web Component with Shadow DOM, bilingual UI, <50 KB gzipped

## Quickstart

```bash
# 1. Scaffold a new project as a sibling of this plugin
/web2rag-init my-customer
cd ../my-customer

# 2. Fill in the env (interactive)
/web2rag-setup

# 3. Start the stack
/web2rag-serve

# 4. Ingest a website (zero outbound cost)
/web2rag-ingest https://docs.acme.com --max-pages 200

# 5. Get the embed snippet for the customer's site
/web2rag-embed-snippet docs.acme.com
# paste the printed <script> tag into the customer's HTML

# 6. (later) audit the bot
/web2rag-audit all --judge anthropic-haiku --output html
```

## Cost contract

- **Ingest is $0.** All scraping + chunking + embedding + guard checks run in-process with free MIT/Apache models.
- **Chat is paid via Anthropic Console** — operator picks `CHAT_MODEL=claude-sonnet-4-6` or `claude-haiku-4-5-20251001` in the generated project's `.env`.
- **Audit is paid** — `--judge anthropic-haiku` runs against Anthropic, `--judge ollama:<model>` runs against an Ollama URL the operator owns.

## Skills

- `/web2rag-init <name>` — scaffold a sibling project
- `/web2rag-setup` — verify prereqs, fill `.env`
- `/web2rag-serve` / `/web2rag-stop` — docker compose lifecycle
- `/web2rag-ingest <url>` — crawl + chunk + embed (configurable politeness: `--force`, `--rate`, `--concurrency`, `--user-agent`)
- `/web2rag-update <site>` — delta re-crawl
- `/web2rag-list-sites` / `/web2rag-remove-site <site>`
- `/web2rag-embed-snippet <site>` — print the `<script>` tag
- `/web2rag-audit quality|capability|redteam|all --judge anthropic-haiku|ollama:<model>` (DeepEval + DeepTeam, four-axis)
- `/web2rag-chat-test "<question>"` — **cost-free preview** via in-session Claude Code subagent (same retrieval + system prompt, real `guard_query`, prompt-rule `guard_output`)
- `/web2rag-chat "<question>"` — **paid** real /chat SSE stream from TUI (~$0.01/msg on Haiku, full Python guards including `guard_output` faithfulness/Detoxify/Presidio)

See `CLAUDE.md` for the full architecture brief.
