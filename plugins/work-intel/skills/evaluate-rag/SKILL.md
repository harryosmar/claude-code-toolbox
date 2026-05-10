---
name: evaluate-rag
description: Evaluate the quality of work-intel's RAG pipeline using RAGAS metrics (Context Precision, Context Recall, Faithfulness, Answer Relevancy). Auto-generates a Q&A testset from ChromaDB on first run. Uses Ollama (free, local) or Anthropic Haiku (subscription) as the LLM judge, configured in config.json. Flags regressions >5% vs prior report. Trigger phrases: "evaluate rag quality", "run ragas", "check retrieval accuracy", "how good is the knowledge base", "ragas evaluation", "test the rag".
allowed-tools: [bash, read, write]
model: sonnet
---

# Evaluate RAG

Run RAGAS metrics against the work-intel ChromaDB knowledge base.

## Hard Rules

- Never modify ChromaDB data during evaluation — read-only.
- Always save the report to `$WORK_INTEL_HOME/ragas-reports/ragas-report-<date>.json`.
- Compare against the most recent prior report and flag any metric that dropped >5%.
- The LLM judge is determined by `config.json` → `judge.provider` (preferred) or legacy `ragas.llm_judge`: `"ollama"` or `"anthropic"`. Both `/work-intel:evaluate-rag` and `/work-intel:security-audit` share the same judge config.
- Never call the Anthropic API directly if `judge.provider` is `"ollama"`.
- See also: `/work-intel:security-audit` — same judge, different concern (security guardrail violations vs. RAG quality).

## Step 1 — Check prerequisites

```bash
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"
python3 -c "import ragas, chromadb, sentence_transformers" 2>&1
```

If any import fails → print install command and stop:
```
pip install ragas chromadb sentence-transformers langchain-anthropic langchain-ollama
```

Check judge config:
```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/offset_store.py" read-config ragas.llm_judge
```

If judge is `"ollama"`: verify Ollama is running (`ollama list`). If not: `brew install ollama && ollama pull llama3.2`.

## Step 2 — Generate or load testset

Check if `$WORK_INTEL_HOME/ragas-testset.json` exists.

**If not exists:**
```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/ragas_generate_testset.py" \
  --config "$WORK_INTEL_HOME/config.json" \
  --output "$WORK_INTEL_HOME/ragas-testset.json" \
  --n-questions 40
```

`ragas_generate_testset.py` samples items from ChromaDB and uses Claude (within this session) to generate Q&A pairs with ground-truth contexts. This is a one-time cost within the Claude Code session.

**If exists:** load it. Print: `Loaded testset: <N> questions`.

## Step 3 — Run evaluation

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/ragas_eval.py" \
  --testset "$WORK_INTEL_HOME/ragas-testset.json" \
  --config "$WORK_INTEL_HOME/config.json" \
  --output "$WORK_INTEL_HOME/ragas-reports/ragas-report-$(date +%Y%m%d-%H%M%S).json"
```

`ragas_eval.py`:
1. Reads `config.json` → selects LLM judge (Ollama or Anthropic Haiku)
2. For each question: retrieves context via `query_chroma.py`, generates answer
3. Runs RAGAS metrics:
   - **Context Precision** — ground-truth based (no LLM)
   - **Context Recall** — ground-truth based (no LLM)
   - **Faithfulness** — LLM judge
   - **Answer Relevancy** — LLM judge

## Step 4 — Compare and report

Load the most recent prior report from `$WORK_INTEL_HOME/ragas-reports/` (by filename date, skip current).

Print report using format from `references/output-format.md`:

```
RAG Evaluation Report — <date>
===================================
Context Precision:   0.87  ✅ (+0.02 vs prior)
Context Recall:      0.79  ✅ (+0.01 vs prior)
Faithfulness:        0.91  ✅ (no change)
Answer Relevancy:    0.84  ⚠️ (-0.06 vs prior — REGRESSION)

Judge: <ollama/llama3.2 or anthropic/claude-haiku-4-5-20251001>
Test set: <N> questions
Report saved: <path>
```

If any metric regressed >5%: print a ⚠️ REGRESSION warning with the affected metrics and suggest re-running setup or re-embedding with a better model.
