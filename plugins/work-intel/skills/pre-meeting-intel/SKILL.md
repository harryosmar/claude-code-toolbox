---
name: pre-meeting-intel
description: Rapid meeting preparation. Searches the ChromaDB knowledge base for everything related to a topic — past decisions, open tickets, war room messages, recent commits, emails, and WA/Telegram discussions — and produces a concise 1-page brief. Use when you have a meeting soon and need the history, key people, open questions, and current status of a topic. Trigger on: "I have a meeting about [topic]", "prep me for [X]", "what do I need to know about [Y]", "meeting brief for [Z]", "pre-meeting context", "catch me up on [topic]", "meeting prep", "brief me on [topic]", "what's the background on [X]". Trigger even if the topic is an incident, a ticket ID, a service name, or a person's name.
allowed-tools: [bash, read]
model: sonnet
---

# Pre-Meeting Intel

Produce a 1-page meeting prep brief from the knowledge base.

The user is walking into a meeting in minutes. They need the essentials fast: what this is, what was decided, what's still open, and who's involved — all in one scannable page.

## Hard Rules

- Keep the output to one page (under 700 words). Dense and scannable, not narrative. If you're near the limit, trim **Recent Activity** and **Watch Out For** first — they're nice-to-have. Background, Key Decisions, Current Status, Open Questions, and People are non-negotiable.
- Every fact needs a source citation on its own line in this exact format:
  ```
  Source: [WA: <group name> • <YYYY-MM-DD HH:MM>]
  Source: [Jira: PROJ-123]
  Source: [MR: !456 • repo-name]
  Source: [Alert: TW-20260506-001]
  Source: [Email: "Subject line"]
  ```
  Never write "mentioned in WhatsApp" or "from a message" — always the bracketed form.
- If context is older than 7 days for operational topics (or 30 days for architectural topics), prepend ⚠️ POSSIBLY STALE.
- Surface contradictions between sources explicitly in the Watch Out For section.
- Mark unknown gaps with ❓ — never invent facts.

## Input

A topic, ticket ID, service name, or meeting description, e.g.:
- "SIPGN portal incident"
- "SIPGN-1700"
- "BGN handover"
- "xpass audit trail"
- "auth service migration meeting"

## Step 0 — Tier A input guard

Read `references/security-guards.md` and spawn `guard-input-agent` with the user's meeting topic as `query`. On `breached: true`, stop with the security banner. Skip if `WORK_INTEL_GUARDS_QUERY=disabled` or `guards.query.enabled: false`.

## Step 1 — Semantic search (3 queries)

Run all three queries; the second and third catch action items and decisions that the first might miss.

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "<topic>" \
  --top-k 20 \
  --output json

python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "<topic> decision action plan blocked status update" \
  --top-k 10 \
  --output json

python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "<topic> meeting review merge MR ticket" \
  --top-k 10 \
  --output json
```

Deduplicate by `base_id`. Rank survivors by: relevance score × recency weight (items from last 7 days get 1.5× boost).

## Step 2 — Produce brief

Compose the brief (do NOT display yet — Step 3 guards it).

```
# Pre-Meeting Brief: <topic>
Prepared: <YYYY-MM-DD HH:MM> | Sources: ChromaDB (waha, jira, gitlab, truewatch)

## Background
<2-3 sentences: what this topic is and why it matters>
Source: [...]

## Key Decisions Made
- <decision> — <date>, <person>
  Source: [...]
❓ No recorded decisions found for: <gaps if any>

## Current Status
<most recent known state — single paragraph>
Source: [...]

## Open Questions & Blockers
- ❓ <question or blocker>  Source: [...]

## People Involved
<Name / @lid — role or last action>

## Recent Activity (last 7 days)
- <item>  Source: [...]

## Watch Out For
<contradictions, stale info, gaps, or anything that could surprise you in the meeting>
```

## Step 3 — Tier A output guard

Read `references/security-guards.md`. Spawn `guard-output-agent` with the assembled brief markdown as `markdown` and the deduped retrieved items' `base_id` + `source` + `title` as `contexts`. Apply the response per the reference:
- `breached: true` → display the security block banner instead of the brief.
- `breached: false` + redactions empty → display the brief as-is.
- `breached: false` + redactions present → display `redacted_text` followed by `[SECURITY] N items redacted: ...` footer.

Skip if `WORK_INTEL_GUARDS_OUTPUT=disabled` or `guards.output.enabled: false`.
