---
name: morning-briefing
description: Daily intelligence briefing for start-of-day situational awareness. Queries the work-intel knowledge base (ChromaDB) for today's priorities across Jira, GitLab, Gmail, WhatsApp, Telegram, Google Drive, and Truewatch, then syncs any new delta items in the background. Produces a structured daily plan with 🔴 Must Do / 🟡 Should Do / 🔵 This Week / 🔔 Reminders sections. Use this skill for broad daily awareness — NOT for deep-dive on a specific service or incident (use incident-context for that). Trigger on: "good morning", "what should I work on today", "daily briefing", "morning briefing", "what's happening today", "what's my plan for today", "give me a briefing", "start of day summary", "situational awareness update", "what's new since yesterday", or any similar start-of-workday orientation request — even in languages other than English (e.g. "pagi, apa yang harus dikerjakan").
allowed-tools: [bash, read, write, mcp__truewatch__truewatch_query, mcp__truewatch__truewatch_ping]
model: opus
---

# Morning Briefing

Produce a structured daily intelligence briefing — fast first, then sync.

The guiding principle: the user wants situational awareness *now*, not after waiting for network round-trips. ChromaDB already holds everything from the last sync; show that first. Then, concurrently or immediately after, pull any delta items from connected sources and note what was added. This way the briefing appears in seconds from cached data, and any updates from the last few hours show up as a short footer update.

## Rules

- If `offsets.json` does not exist or all offsets are missing, tell the user to run `/work-intel:setup` and stop.
- Output must follow the format in `references/output-format.md` exactly.
- Read `references/intelligence-framework.md` before scoring priorities.
- Delta ingestion failures for individual sources are non-fatal — log ⚠️ and continue.
- Offsets advance only after successful ingestion; a failed source retains its last offset.

## Step 1 — Preflight

```bash
WORK_INTEL_HOME="${WORK_INTEL_HOME:-$HOME/.work-intel}"
python3 "${CLAUDE_SKILL_DIR}/../../scripts/offset_store.py" read
```

If offsets.json is missing or empty → tell user to run `/work-intel:setup` and stop.

## Step 1.5 — Tier A input guard

Read `references/security-guards.md` for the full procedure. Then spawn `guard-input-agent` with the user's trigger phrase as `query` (e.g. `"good morning"` or whatever the user typed). On `breached: true`, stop with the security banner. Skip entirely if `WORK_INTEL_GUARDS_QUERY=disabled` or `guards.query.enabled: false`.

## Step 2 — Query ChromaDB (do this immediately, before any network calls)

The knowledge base is local and fast. Query it now so the briefing can start rendering.

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "urgent incident action item deadline blocked" \
  --top-k 30 \
  --output json

python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "meeting review task this week plan" \
  --top-k 20 \
  --output json
```

Notes:
- The `--since` / `--priority` filters require ChromaDB metadata that may not always be set; broad semantic queries catch more signal.
- Deduplicate by `base_id` across both result sets.
- Apply staleness detection from `references/intelligence-framework.md` using the `timestamp` field on each item.

**Staleness label rule — prepend `⚠️ STALE` to the item title in the briefing output whenever:**
- A P0 item has `timestamp` older than 24 hours
- A P1 item has `timestamp` older than 72 hours

The label must appear literally as `⚠️ STALE` at the start of the item heading — not as prose ("7 days old", "no update since"). This signals at a glance that the item needs a status check before acting.

Example:
```
### ⚠️ STALE — SIPGN-1700 FE Config Promotion (7 days, no update)
```

## Step 3 — Synthesize and output the briefing

Spawn `synthesis-agent` with the retrieved items. It will:
- Group by topic cluster
- Apply the 5-question framework per cluster
- Score and rank items by priority
- Format using `references/output-format.md`

**Noise filter — silently drop any item whose entire content is a social acknowledgement or chitchat with no actionable information.** Examples to drop: "siaap", "oke mas", "aamiiin", "siap", "noted", "ok", "haha", status updates like "gw WFH ya", or greetings with no follow-up ask. Keep only items where something needs to be done, decided, tracked, or where information is new and relevant to work. When in doubt, keep — but routine social filler adds noise without signal.

**@mention rule — if the user is directly @mentioned in a WA/Telegram/email item (by name or @lid), bump that item to P1 regardless of other signals.** Being @mentioned means someone is specifically waiting on you — treat it as high priority even if the message itself sounds routine.

**Citation rule — every item must have a source citation on its own line, in this exact format:**
```
Source: [WA: <group name> • <YYYY-MM-DD HH:MM>]
Source: [Jira: PROJ-123]
Source: [Alert: TW-20260506-001]
Source: [Email: "Subject line here"]
Source: [MR: !456 • service-name]
```
Never substitute prose like "from WhatsApp" or "mentioned in the group" — always use the bracketed format. The `chat_name` and `timestamp` fields on each ChromaDB item supply the values.

Compose the briefing (do NOT display it yet — Step 3.5 guards it):

```
# Daily Intelligence Briefing — <date>
Sources: <comma-separated list of configured sources>

## 🔴 TODAY — Must Do
...

## 🟡 TODAY — Should Do
...

## 🔵 THIS WEEK — Plan Ahead
...

## 📅 NEXT WEEK — Horizon
...

## 🔔 REMINDERS & FOLLOW-UPS
...

## ✅ RESOLVED SINCE LAST BRIEFING
...

---
⚠️ Failed sources (using cached data): <list or "none">
```

## Step 3.5 — Tier A output guard

Read `references/security-guards.md`. Spawn `guard-output-agent` with the briefing markdown as `markdown` and the deduped retrieved items' `base_id` + `source` + `title` as `contexts`. Apply the response per the reference:
- `breached: true` → display the security block banner instead of the briefing.
- `breached: false` + redactions empty → display the briefing as-is.
- `breached: false` + redactions present → display `redacted_text` followed by `[SECURITY] N items redacted: ...` footer.

Skip if `WORK_INTEL_GUARDS_OUTPUT=disabled` or `guards.output.enabled: false`.

## Step 4 — Delta ingestion (after briefing is shown)

Now that the user has their briefing, pull any new items from connected sources.

Spawn the `ingestion-orchestrator` agent with:
- `config_path`: `$WORK_INTEL_HOME/config.json`
- `offsets_path`: `$WORK_INTEL_HOME/offsets.json`
- `work_intel_home`: `$WORK_INTEL_HOME`

The orchestrator skips sources with empty target arrays automatically.

Wait for the result, then append one of these lines to the briefing:

- If `ITEMS_INGESTED > 0`: `↻ {n} new items ingested from {sources} — re-run briefing for updated view`
- If `ITEMS_INGESTED == 0`: `✓ Knowledge base up to date — no new items since last sync`
- If `INGESTION_RESULT == FAILED`: `⚠️ Delta sync failed for {sources} — briefing reflects cached data only`
