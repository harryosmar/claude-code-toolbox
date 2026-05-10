---
name: web2rag-chat
description: Exercise the FULL production /chat path of a generated web2rag chatbot from the Claude Code TUI — same path the embedded widget uses. Calls POST /chat with SSE streaming, runs the real Python guard_query + hybrid retrieval + Claude with native Citations API + real Python guard_output (faithfulness check + Detoxify + Presidio + citation integrity), and prints token-by-token to the terminal. **Paid** — billed against the operator's ANTHROPIC_API_KEY (~$0.01/msg on Haiku, ~$0.025/msg on Sonnet). Use whenever the operator wants to test the chatbot via the production code path from the TUI rather than the widget — phrasings like "run a real chat", "ask the bot for real", "test /chat with real guards", "send a question through the production path", "live chat from terminal", "tes /chat sungguhan dari terminal" (id). Also trigger when the user explicitly says "production", "with real guards", "real LLM", or "with my API key" alongside a question. **Distinct from `/web2rag-chat-test`** which is the cost-free preview with prompt-rule guards (this skill is the paid mirror with full Python guard middleware). Do NOT use to iterate cheaply on prompts (use web2rag-chat-test), to ingest content (web2rag-ingest), or to run audits (web2rag-audit).
allowed-tools: [bash, read]
---

# web2rag-chat

Production chat from the TUI. Same SSE stream the widget consumes, just rendered as text.

## Inputs

- `<message>` — the question (required, EN or ID — production guards run regardless of language)
- `--site-id <id>` — restrict retrieval to one site (default: derive from cwd's `data/sites.json`; if multiple, ask)
- `--api-url <url>` — defaults to `http://localhost:8787`
- `--project <path>` — target project (default: cwd)

## What this skill does

1. Pre-flight: confirm `<api-url>/health` returns `status=ok` and that `ANTHROPIC_API_KEY` is non-placeholder. Surface the non-placeholder check by reading `.env` (in the project) — if it's still `sk-ant-placeholder...`, refuse and point to `/web2rag-setup`.

2. POST `<api-url>/chat` with `{message, site_id, history: []}` and `Accept: text/event-stream`. Stream the response. The api orchestrates:
   - `guard_query` (real Python middleware)
   - `rewrite` (LLM call — only fires for multi-turn, skipped here)
   - `hybrid_search` + `rerank`
   - Claude with native Citations API (token stream)
   - `guard_output` (real Python — may emit a `guard_amend` event if it rewrote)

3. Render to the terminal as the SSE events arrive:
   - `event: token` → append text under "ASSISTANT REPLY" header
   - `event: guard_amend` → replace the streamed answer with the corrected text + a "(corrected by safety filter)" badge
   - `event: citations` → list each citation under "Sources"
   - `event: blocked` → print "BLOCKED by guard_query" + reasons + STOP
   - `event: done` → print usage (input/output tokens, $$$ estimate, latency, guards summary) and STOP

4. Print the cost footer: actual `input_tokens`/`output_tokens` from the `done` event, multiplied by the per-token rate of `CHAT_MODEL` (`$1/M in, $5/M out` for Haiku 4.5; `$3/M in, $15/M out` for Sonnet 4.6) → ~per-message cost.

## Output shape

```
/web2rag-chat "apa itu Badan Gizi Nasional?"
────────────────────────────────────────────
site:        www.bgn.go.id
chat_model:  claude-haiku-4-5-20251001
api:         http://localhost:8787

guard_query: passed (severity=low)

─── ASSISTANT REPLY (streaming) ────────────────────────────────────
Badan Gizi Nasional (BGN) adalah lembaga pemerintah non-kementerian
yang berada di bawah dan bertanggung jawab kepada Presiden, dengan
tugas melaksanakan pemenuhan gizi nasional...
                                                       ↑
                                            (tokens arrive live)
────────────────────────────────────────────────────────────────────

Sources:
  [0] https://www.bgn.go.id/functions-duties (char 12-156)
  [1] https://www.bgn.go.id/functions-duties (char 240-380)

guard_output: passed (severity=low)
              reasons: faithfulness_strip:0_sentences

usage:    input=1247  output=189  total=1436
cost:     ~$0.0021 (claude-haiku-4-5-20251001 @ $1/M in, $5/M out)
latency:  1842ms (incl. retrieval + rerank + LLM)
```

## When `/web2rag-chat-test` vs `/web2rag-chat`

| Question | Use this |
|---|---|
| "How will the bot respond? I want to iterate quickly." | `/web2rag-chat-test` ($0) |
| "Does the production faithfulness check strip my answer?" | `/web2rag-chat` (paid; only the real Python guard_output answers this) |
| "I'm tuning the system prompt." | `/web2rag-chat-test` first, then a single `/web2rag-chat` to confirm |
| "Customer is on the line, I need the real answer." | `/web2rag-chat` |
| "I want to red-team the bot with adversarial inputs." | `/web2rag-audit redteam` (DeepTeam over many attacks at once) |

## Inviolable rules

- **Refuse if `ANTHROPIC_API_KEY` is still the scaffolded placeholder.** Print actionable error pointing to `/web2rag-setup`. The skill must never silently make a request that will 401 — it wastes a round-trip and confuses the operator.
- **Always print the cost footer.** Operators need a continuous reminder that this path costs money; surprise bills are a worse failure mode than verbose output.
- **Pass through the api's SSE events verbatim shape.** Do not invent a different event taxonomy — the goal is parity with what the widget sees, so debugging "production gives a weird answer" is the same workflow whether it came from the widget or the TUI.
