---
name: web2rag-audit
description: Evaluate a generated web2rag chatbot across the four-axis model — Correctness (DeepEval Faithfulness/AnswerRelevancy/ContextualPrecision/ContextualRecall), Capability (DeepEval GEval over helpfulness/task-completion/scope-adherence + production thumbs-up/down feedback rolled up from `data/feedback.jsonl`), Security (DeepTeam jailbreak/prompt-injection/bias/toxicity/prompt-leakage simulators), and Cost (per-call input/output token counts already captured from Anthropic's response.usage). The judge LLM is switchable per-run: `--judge anthropic-haiku` (paid via Anthropic Console), `--judge ollama:<model>` (free if the operator owns the GPU host, native Ollama API), `--judge openai-compat:<model>` (any OpenAI-compatible /v1 server at OLLAMA_URL), or `--judge custom-openai:<url>:<model>` (OpenAI-compatible with the URL passed inline — self-contained per run, no env edit). Use whenever the user wants to evaluate, audit, score, or red-team a web2rag chatbot — phrasings like "audit the bot", "run DeepEval", "red-team the chatbot", "check answer faithfulness", "measure helpfulness", "jalankan eval" (id), "run security tests on the bot", "DeepTeam the chatbot", "is the bot safe". Trigger across the four subtypes: `quality`, `capability`, `redteam`, `all`. Do NOT use to ingest content, to start the stack, or to stop the stack.
allowed-tools: [bash, read, write]
---

# web2rag-audit

Run DeepEval + DeepTeam against the generated project's chatbot.

## Inputs

- `<mode>` — one of `quality | capability | redteam | all` (required)
- `--judge <spec>` (default `anthropic-haiku`). One of:
  - `anthropic-haiku` — Claude Haiku 4.5, paid via Anthropic Console.
  - `ollama:<model>` — native Ollama API at `OLLAMA_URL` (free if you own the host).
  - `openai-compat:<model>` — OpenAI-compatible `/v1/chat/completions` at `OLLAMA_URL`. Works with vLLM, LM Studio, llama.cpp's server, Ollama's OpenAI shim.
  - `custom-openai:<url>:<model>` — same wire protocol as `openai-compat`, but the URL is inline so the flag is self-contained (no `.env` edit, no operator coordination). The URL must include `/v1`. Quote the whole flag value because it contains colons. Example:
    ```
    --judge 'custom-openai:http://192.0.2.5:8001/v1:Qwen/Qwen2.5-14B-Instruct-AWQ'
    ```
    Caveat: the model id must not contain `:` (Ollama-style `qwen2.5:14b` tags break the parser — use `openai-compat:<model>` with `OLLAMA_URL` set instead).
    Concurrency: set `JUDGE_MAX_CONCURRENCY` in `.env` to the judge server's `--max-num-seqs` (default 8). DeepEval/DeepTeam fan out hundreds of judge calls; without this cap, a small vLLM box queues them and every call pays queue-wait latency.
- `--testset-size N` (default 50; only used when mode includes `quality` or `capability`)
- `--output html | md | json` (default `html`)
- `--locales en,id` — generate test set in EN + ID (default both)
- `--project <path>` — target project (default cwd)

## Four-axis model

```
        CORRECTNESS                              SECURITY
     "is the answer right?"                  "can it be broken?"
  DeepEval (Faithfulness, AnswerRelevancy,        DeepTeam
  ContextualPrecision, ContextualRecall)
              │                                     │
              └─────────────┬─────────────┬─────────┘
                            │             │
                ┌───────────┴─────────────┴───────────┐
                │                                     │
            CAPABILITY                              COST
   "does it solve the user's job?"        "is it sustainable at scale?"
   DeepEval GEval(helpfulness,             input/output tokens captured per
   task_completion, scope_adherence)        chat from Anthropic response.usage
   + production thumbs-up/down              + retrieval/rerank/guard latency
   (POST /feedback → data/feedback.jsonl)
```

## Two-tool architecture, four eval axes

| Axis | Tool | Why |
|---|---|---|
| Correctness | **DeepEval** Faithfulness/AnswerRelevancy/ContextualPrecision/ContextualRecall | RAG-specific, well-validated, ships Synthesizer for Q&A test-set generation |
| Capability | **DeepEval GEval** + production `/feedback` | GEval is the auto-eval proxy; thumbs-up/down is the source of truth |
| Security | **DeepTeam** | Owns the attack-simulation surface; built on top of DeepEval (transitive dep) |
| Cost | Anthropic `response.usage` per chat | already captured in /chat done event; aggregated in audit report |

Live PII / hallucination / toxicity per chat call is enforced by `guard_query` + `guard_output`, not by the audit pipeline. The audit's `guard_replay` section reports those guards' hit rates against the test set so operators can tune thresholds.

## What this skill does

1. POSTs to the api's `/audit` endpoint with the parsed flags.
2. The api orchestrates the appropriate runners. `quality` → DeepEval correctness metrics; `capability` → DeepEval GEval + feedback rollup from `/feedback/summary`; `redteam` → DeepTeam (`Bias`, `Toxicity`, `PromptLeakage` vulnerabilities × `PromptInjection`, `ROT13`, `LinearJailbreaking` attacks); `all` → all three.
3. Test set is generated via DeepEval `Synthesizer` from the ingested corpus and cached in `eval/testsets/`. Re-used across runs unless `--regenerate` is passed.
4. The audit also replays `guard_query` over the test-set prompts and reports the per-severity hit rate.
5. The audit report lands in `reports/audit_<ts>.<ext>`.

## Inviolable rules

- **Audit must not pollute the live chroma collection.** Test-set generation queries it read-only; chat responses during audit are not written back as new training data.
- **Judge swap is per-run.** Setting `--judge ollama:llama3.1` (or `--judge custom-openai:...`) for ONE audit run does NOT change the chat path's `CHAT_MODEL`; the widget keeps using Claude.
- **Production feedback is the source of truth for capability.** GEval is a CI-friendly proxy, but real signal comes from `data/feedback.jsonl` (today's backend) — when feedback diverges from GEval, trust feedback.
