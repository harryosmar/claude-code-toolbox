---
name: security-audit
description: Run deepteam guardrails (Tier C batch) over a sample of work-intel's ChromaDB docs, the last 50 skill outputs, and the last 200 user queries to detect PII leaks, prompt-injection traces, toxicity, illegal content, or off-topic drift. **This skill is for SECURITY concerns — for RAG retrieval quality use /work-intel:evaluate-rag instead.** Produces a violation report with per-guard pass/fail counts and regression detection vs prior runs (>5% pass-rate drop flagged). Use when the user wants a security health check on the knowledge base, suspects sensitive data leaked into a briefing, or wants to verify Tier A + Tier B inline guards aren't drifting. Trigger phrases — "audit work-intel security", "run guardrail scan", "check for PII leaks", "scan for sensitive data in work-intel", "deepeval audit", "deepteam audit", "security review of work-intel", "audit keamanan work-intel", "cek bocor data sensitif", "scan kebocoran PII".
allowed-tools: [bash, read, write]
model: sonnet
---

# Security Audit (Tier C batch)

Replay deepteam guardrails over recent work-intel state and write a structured report.

## Hard Rules

- Read-only on ChromaDB and log files. Never modify the knowledge base.
- Always save reports to `$WORK_INTEL_HOME/security-audit/security-audit-<UTC-ts>.{json,md}`.
- Compare against the most recent prior report; flag any pass-rate that dropped >5%.
- Raw pre-redaction text goes to `$WORK_INTEL_HOME/security-audit/_raw/<UTC-ts>/` — this directory is excluded from `/work-intel:backup`.
- The LLM judge is determined by `config.json` → `judge.provider`: `"anthropic"` (Haiku) or `"ollama"` (with optional remote `ollama_base_url`). Anthropic requires `ANTHROPIC_API_KEY` env var.
- Never call the Anthropic API if judge.provider is `"ollama"`.
- Honor the `WORK_INTEL_GUARDRAILS=disabled` kill switch — exit cleanly without running.

## Step 1 — Preflight

```bash
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"

# Required deps
python3 -c "import chromadb, deepteam" 2>&1
```

If any import fails, print the install command and stop:
```
pip install chromadb deepteam
```

```bash
# Judge backend reachable?
python3 "${CLAUDE_SKILL_DIR}/../../scripts/_llm_judge.py" --health-check
```

If exit code != 0: print the message verbatim and stop. Tell the user to fix `judge.provider` or `judge.ollama_base_url` in `~/.work-intel/config.json`, OR to set `ANTHROPIC_API_KEY`.

## Step 2 — Run the audit

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/security_audit.py" \
  --config "$WORK_INTEL_HOME/config.json" \
  --output-dir "$WORK_INTEL_HOME/security-audit"
```

The script:

1. Samples up to N (config: `security_audit.sample_size`, default 200) ChromaDB docs across all collections, with a fixed `random.seed(42)` so the sample is stable across runs (good for diff vs prior).
2. Loads the last 200 user queries from `$WORK_INTEL_HOME/query-log.jsonl` (skipped if missing — first-run case).
3. Loads the last 50 skill outputs from `$WORK_INTEL_HOME/output-log/*.md` (skipped if missing).
4. Builds a `deepteam.guardrails.Guardrails` instance using the shared `_llm_judge` (via `_deepeval_adapter`).
5. Replays each input through `guard_input(...)` and each output through `guard_output(...)`. Collects per-guard verdicts.
6. Writes:
   - `security-audit-<ts>.json` (machine-readable, full verdicts)
   - `security-audit-<ts>.md` (human-readable, per-guard pass/fail tables)
   - `_raw/<ts>/` (raw text snippets for triage; excluded from backup)
7. Compares against the previous report; exit code 2 if any pass-rate dropped >5%.

Expected runtime: 1–5 minutes depending on sample size and judge (Anthropic ≈ 1s/call, remote Ollama ≈ 2–4s/call).

## Step 3 — Surface the report

Print the contents of the markdown report to the user:

```bash
cat "$WORK_INTEL_HOME/security-audit/$(ls -t $WORK_INTEL_HOME/security-audit/ | head -1)"
```

If the script exited with code 2 (regression):
- Prepend: `⚠️ REGRESSION DETECTED — at least one pass-rate dropped >5% vs the previous run.`
- Suggest running `/work-intel:evaluate-rag` to rule out a RAG-quality regression first; if RAGAS is also down, the issue is upstream of guards.

If many breaches are concentrated in one base_id, suggest the user add it to `guards.allowlist` in `~/.work-intel/config.json` if the source is known-safe.

## Step 4 — Cleanup hint

Mention to the user:
- Reports under `security-audit/` are kept indefinitely; they're small (KB-range) and useful for trend lines.
- `_raw/` snapshots are bigger (MB-range) and excluded from backup. Delete old `_raw/<ts>/` directories when disk is tight.

## See also

- `/work-intel:evaluate-rag` — RAG quality eval (RAGAS metrics — different concern).
- `references/output-format.md` — citation rules used by all skills.
