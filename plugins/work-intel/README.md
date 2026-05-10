# work-intel

Context Intelligence plugin for Claude Code. Aggregates all your work channels into a local vector knowledge base and surfaces what matters.

## What it does

- **Delta ingestion**: pulls only new/updated items from every source since last fetch
- **Vector search**: all items embedded locally with `jina-embeddings-v5-text-small` into ChromaDB
- **Daily briefing**: structured plan with 🔴 Must Do / 🟡 Should Do / 🔵 This Week
- **Incident context**: fast 5-question root-cause brief from any alert or service name
- **Meeting prep**: 1-page brief from the knowledge base on any topic
- **Document ingestion**: index local PDFs, DOCX, XLSX, PPTX, images into the knowledge base
- **Security guardrails**: three-tier — Claude Code subagents on chatbot input/output, local Presidio PII redaction at ingest (multilingual EN+ID), batch deepteam audit
- **RAG evaluation**: RAGAS metrics (Precision, Recall, Faithfulness, Relevancy) with Ollama or Anthropic Haiku as judge
- **Portable**: one tarball backup, restore on any host with `migrate.py`

## Data sources

| Source | MCP Server |
|---|---|
| Jira | `atlassian-mcp-server` (official, GA) |
| GitLab | `glab` CLI |
| Gmail | `@gongrzhe/server-gmail-autoauth-mcp` |
| WhatsApp | `dudu1111685/waha-mcp` (via WAHA Docker) |
| Telegram | `sparfenyuk/mcp-telegram` |
| Google Docs/Sheets/Slides | `aaronsb/google-workspace-mcp` |
| Truewatch APM | `mcp__truewatch` (pre-installed) |
| Local files | `unstructured[all-docs]` |

## Cost model

Zero external API cost for day-to-day chatbot use. The Anthropic API is only touched if you opt into batch eval (`/work-intel:evaluate-rag`, `/work-intel:security-audit`) with `judge.provider: anthropic`.

| Layer | Technology | API key needed? |
|---|---|---|
| Embeddings | `jina-embeddings-v5-text-small` via `sentence-transformers` (local, multilingual) | No |
| Vector DB | ChromaDB (local persistent) | No |
| Text extraction | `unstructured[all-docs]` | No |
| Tier A inline guards (chatbot) | Claude Code subagents (Sonnet/Haiku, in-session) | No |
| Tier B inline guards (ingest) | Microsoft Presidio + spaCy `xx_ent_wiki_sm` (or `xlm-roberta` opt-in) | No |
| Tier C batch judge (RAGAS + security-audit) | Anthropic Haiku (subscription) OR remote Ollama (free) | Only when batch eval runs |
| Skills/agents | Claude Code session (subscription) | No |

## Security guardrails (three tiers)

work-intel ships defense-in-depth on top of the RAG pipeline:

1. **Tier A — Chatbot input/output (free, no API key).** `guard-input-agent` (a Sonnet subagent in your session) scans every user query for prompt injection / jailbreak / off-topic / cybersecurity asks. `guard-output-agent` scans every synthesized briefing for residual PII (multilingual incl. Bahasa), toxicity, illegal content. Wired into `morning-briefing`, `incident-context`, `pre-meeting-intel`. Configurable model: `sonnet` (default, accurate) or `haiku` (faster).
2. **Tier B — Ingest PII redaction (free, no API key).** Microsoft Presidio + spaCy `xx_ent_wiki_sm` (or opt-in `cahya/xlm-roberta-base-indonesian-NER` for higher Bahasa recall) runs over every embed call. Catches emails, phones, names, Indonesian NIK / NPWP / BPJS. Failed redactions are logged but never block ingestion (fail-open).
3. **Tier C — Batch security-audit (Anthropic API or remote Ollama).** `/work-intel:security-audit` replays deepteam guards over a sample of ChromaDB docs + recent queries + recent outputs. Mirrors `evaluate-rag` shape with regression detection (>5% pass-rate drop flagged).

Per-tier kill switches: `WORK_INTEL_GUARDS_INGEST=disabled`, `WORK_INTEL_GUARDS_QUERY=disabled`, `WORK_INTEL_GUARDS_OUTPUT=disabled`, `WORK_INTEL_GUARDRAILS=disabled` (kills Tier C audit). Per-doc allowlist via `guards.allowlist: ["sha", ...]` in `~/.work-intel/config.json`.

## Setup

### 1. Install Python dependencies

```bash
pip install \
  sentence-transformers \
  chromadb \
  "unstructured[all-docs]" \
  ragas datasets \
  langchain-anthropic \
  langchain-ollama \
  langchain-community \
  presidio-analyzer \
  presidio-anonymizer

# spaCy models for Tier B PII (multilingual EN+ID)
python -m spacy download en_core_web_sm
python -m spacy download xx_ent_wiki_sm

# Optional — only needed for /work-intel:security-audit (Tier C batch)
pip install deepteam
```

### 2. Install MCP servers

See `references/mcp-setup.md` for full setup instructions per source.

### 3. Initialize

```
/work-intel:setup
```

This collects your source config (WA groups, Telegram channels, Jira projects, etc.), performs a full historical fetch from each, and prepares ChromaDB. Run once.

## Skills

| Skill | Trigger |
|---|---|
| `/work-intel:setup` | First-time setup, add new sources |
| `/work-intel:morning-briefing` | Daily plan |
| `/work-intel:incident-context` | Root-cause brief for an alert |
| `/work-intel:pre-meeting-intel` | Meeting prep on a topic |
| `/work-intel:ingest-document` | Add a local file to the knowledge base |
| `/work-intel:evaluate-rag` | RAGAS quality evaluation |
| `/work-intel:security-audit` | Deepteam guardrail audit (PII / injection / toxicity sweep over ChromaDB + recent queries/outputs) |
| `/work-intel:backup` | Create portable data tarball |
| `/work-intel:restore` | Restore on a new host |

## Portability

All data lives under `$WORK_INTEL_HOME` (default `~/.work-intel/`):

```
~/.work-intel/
├── config.json           # source config + embedding model pin + judge + guards
├── offsets.json          # per-source delta cursors
├── chroma/               # ChromaDB vectors (portable)
├── ragas-reports/        # RAGAS evaluation history
├── security-audit/       # deepteam guardrail audit reports
│   └── _raw/             # raw pre-redaction text (excluded from backup)
├── output-log/           # recent skill outputs (consumed by security-audit)
└── query-log.jsonl       # recent queries (consumed by security-audit)
```

To migrate to a new host:
1. `/work-intel:backup` → creates `work-intel-backup-<date>.tar.gz`
2. Copy to new host
3. `/work-intel:restore backup.tar.gz`

OAuth tokens (Gmail, Google Drive) are device-bound and excluded from backup. `restore` guides you through re-authentication.
