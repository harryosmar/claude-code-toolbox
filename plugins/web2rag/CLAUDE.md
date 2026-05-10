# web2rag — scaffolder plugin

This repo is a Claude Code **scaffolder plugin** (like `peruri-go-scaffolder`). It does not run a RAG service itself — it generates one. When the user invokes `/web2rag-init <name>`, the plugin copies + renders `templates/` into a sibling directory `../<name>/`, producing a fully self-contained project the user can `git init`, modify, and deploy.

Two repos at runtime:

```
port-web2rag/                  ← THIS PLUGIN (skills + templates)
                               │
                               │  /web2rag-init my-customer
                               ▼
my-customer/                   ← GENERATED PROJECT (sibling)
```

After scaffolding, all other skills (`/web2rag-ingest`, `/web2rag-serve`, `/web2rag-audit`, ...) operate **inside the generated project**. They accept an optional `--project <path>` flag; without it they assume cwd.

## Cost contract (hard requirement)

| Phase | Provider | Cost |
|---|---|---|
| Ingest (scrape → guard_ingest → chunk → embed → store) | Crawl4AI + Prompt-Guard-2 + Presidio + BGE-M3 + Chroma — all in-process | **$0** |
| Chat (guard_query → retrieval → LLM → guard_output → SSE) | Claude via Anthropic Console (`CHAT_MODEL=claude-sonnet-4-6` or `claude-haiku-4-5-20251001`) | paid (Anthropic only) |
| Audit / eval (DeepEval correctness+capability + DeepTeam security) | Switchable judge: `--judge anthropic-haiku`, `--judge ollama:<model>` (native Ollama API at `OLLAMA_URL`), `--judge openai-compat:<model>` (OpenAI-compatible `/v1` at `OLLAMA_URL`), or `--judge custom-openai:<url>:<model>` (URL inline, self-contained) | paid (Anthropic) or free (self-hosted) |

Anything that would charge during ingest is rejected. Guards are free-by-default; `GUARD_MODE=llm` opts into a paid Haiku second-pass.

## Repo layout

```
port-web2rag/
├── .claude-plugin/plugin.json     manifest
├── CLAUDE.md                      this file
├── README.md                      user-facing quickstart
├── skills/                        slash-command surface
│   ├── web2rag-init/              scaffolds a sibling project (uses templates/)
│   ├── web2rag-setup/             prereq check + interactive .env fill
│   ├── web2rag-ingest/            crawl + guard_ingest + chunk + embed
│   ├── web2rag-update/            delta re-crawl by lastmod/hash
│   ├── web2rag-list-sites/
│   ├── web2rag-remove-site/
│   ├── web2rag-serve/             docker compose up -d
│   ├── web2rag-stop/              docker compose down
│   ├── web2rag-embed-snippet/     prints the <script> tag for the widget
│   ├── web2rag-audit/             RAGAS / deepeval / deepteam runners
│   ├── web2rag-chat-test/         cost-free preview via in-session subagent ($0)
│   └── web2rag-chat/              real /chat SSE stream from TUI (paid, full guards)
├── templates/                     blueprint of the GENERATED project
│   ├── CLAUDE.md.tmpl
│   ├── docker-compose.yml.tmpl
│   ├── docker/Dockerfile.api
│   ├── pyproject.toml.tmpl
│   ├── server/                    FastAPI app (full tree)
│   ├── widget/                    Web Component + Shadow DOM
│   └── eval/                      judges + runners
└── references/                    bundled docs the skills point at
```

## What lives where (rule of thumb)

- A skill body defines **WHEN** to run + **HOW** to invoke (CLI surface). It calls scripts or template-rendering helpers.
- Per-language behaviour, model wiring, and runtime logic live in `templates/server/` so they end up inside every generated project. Editing those files **does not** affect already-scaffolded projects — re-scaffolding (or copying the new file in by hand) does.
- Cross-cutting docs live in `references/` and are read by skills via relative paths.

## 3-guard architecture (in the generated project)

Three middleware functions in the FastAPI flow, all free by default:

- `guard_ingest` — per scraped page, before chunking. Drops/redacts prompt-injected HTML, PII, oversized payloads.
- `guard_query` — per chat message, before retrieval. Catches direct jailbreaks, scope drift, user-side PII.
- `guard_output` — after LLM, before SSE flush. Faithfulness check (claim ↔ retrieved chunk cosine sim), Detoxify, Presidio leak check, citation-integrity dict-lookup.

Each returns `GuardResult { passed, severity: low|med|high, reasons[], redacted_text? }`. The SSE `done` event includes a `guards` field so the widget can render a "moderated" badge and the audit can replay decisions.

## Cost levers (operator-tunable)

- **Context compression**: hybrid search retrieves `RETRIEVE_TOP_K=10` candidates, reranker keeps `RERANK_TOP_N=3`. top_k controls the dominant CPU cost of /retrieve and /chat (each candidate is one cross-encoder forward pass); 10 is the cost/quality sweet spot for most corpora — bump to 20 only for very dense docs sites where the dense+BM25 fusion misses good chunks in the top 10. Each rerank rejection drops ~2 KB from the LLM prompt — three chunks is the sweet spot for most sites; bump to 5 only for very dense corpora. `RERANK_BATCH_SIZE=32` caps cross-encoder forward-pass memory; matters only when operators bump top_k well above the default. /retrieve accepts per-request `retrieve_top_k` and `rerank_top_n` overrides for ad-hoc tuning without restarting the api.
- **Anthropic Batch API**: `AUDIT_USE_BATCH_API=true` routes /audit's judge calls through the 50%-off Batch API (24h SLA, usually finishes in minutes). Wired into testset generation today; per-sample DeepEval grading remains realtime because deepeval's `metric.measure()` loop is synchronous and per-prompt. Set on CI cron; leave off for ad-hoc audits.
- **Chat is always realtime.** Batch API is opt-in for /audit only; do not route /chat through it.

## Invariants (don't break these)

1. **Ingest must be $0.** Anything that calls Anthropic or another paid API during `/ingest` is wrong.
2. **Chat is Claude-only.** No Ollama-on-chat path; manual citation extraction code does not exist. Native Anthropic Citations API is always used.
3. **Eval is a TWO-tool stack across FOUR axes.** DeepEval for correctness + capability (Faithfulness, AnswerRelevancy, ContextualPrecision/Recall, GEval over helpfulness/task-completion/scope-adherence, Synthesizer for test-set gen). DeepTeam for security/red-team (jailbreak, prompt injection, bias, toxicity, prompt leakage simulators). One framework family, one judge factory, one test-case shape. Per-call PII / hallucination / toxicity is already enforced live by `guard_query` + `guard_output`; running DeepEval's overlapping metrics again at audit time would double-count, so we don't. `eval/judges/factory.py` returns `AnthropicModel` for `anthropic-haiku`, native-Ollama POST-to-`OLLAMA_URL` for `ollama:<model>`, OpenAI-compatible POST-to-`OLLAMA_URL` for `openai-compat:<model>`, and OpenAI-compatible POST-to-inline-URL for `custom-openai:<url>:<model>`; `quality.py` + `capability.py` (DeepEval) and `redteam.py` (DeepTeam) all consume it through the same `JudgeLLM.generate()` shape.
4. **Bilingual EN + ID is first-class.** BGE-M3 multilingual embeddings + bundled widget `i18n/{en,id}.json` + system-prompt instructs the LLM to mirror the user's question language. Not a feature flag.
5. **Politeness is configurable.** `RESPECT_ROBOTS_TXT`, `CRAWL_RATE_LIMIT`, `CRAWL_CONCURRENCY`, `CRAWL_USER_AGENT` are all overridable via `.env` and per-ingest CLI flags. Override decisions are logged into the audit report.

## Critical files

- `templates/server/store/chroma.py` — defines the citation metadata schema; everything reads/writes through this.
- `templates/server/store/feedback.py` — pluggable production-feedback store. JsonlFeedbackStore today; swap in PostgresFeedbackStore / S3 / ClickHouse later by editing only `make_store()`.
- `templates/server/llm/claude.py` — Anthropic SDK + native Citations API; no provider switch.
- `templates/eval/judges/factory.py` — the judge swap.
- `templates/widget/src/widget.js` — Web Component definition; sends thumbs-up/down to `/feedback` after every answer.
- `references/citation-schema.md` — single source of truth for citation flow end-to-end.
