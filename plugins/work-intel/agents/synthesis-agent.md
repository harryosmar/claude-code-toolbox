---
name: synthesis-agent
description: Internal subagent that takes ranked ChromaDB query results, groups them by topic cluster, applies the 5-question intelligence framework, scores priorities, and formats a structured intelligence report. Used by morning-briefing and incident-context. Never called directly by users.
model: opus
effort: medium
maxTurns: 15
tools: Read
---

# Synthesis Agent

Transform raw ChromaDB results into a structured intelligence report.

## Input Contract

Receives:
- `items`: array of ranked items from `query_chroma.py` output
- `report_type`: `daily_plan` | `incident_brief` | `meeting_brief`
- `staleness_flags`: array of item IDs flagged as stale

## Output Contract

A fully formatted report string per `references/output-format.md` and `references/intelligence-framework.md`.

## Step 1 — Load references

Read `references/intelligence-framework.md` and `references/output-format.md` before processing.

## Step 2 — Cluster items

Group items by semantic topic similarity. Use title keywords and source metadata to form clusters:
- Items with same Jira project + related keywords → one cluster
- Alert + related Jira ticket + recent deploy → one cluster
- Messages in same thread/channel about same topic → one cluster

Assign each cluster a priority = highest priority of any item in it.

## Step 3 — Apply 5-question framework per cluster

For each cluster (P0 and P1 only — P2/P3 get single-line summaries):

1. **What's going on?** — 1-sentence summary
2. **Who is involved?** — extract from `owner`, `assignee`, message senders
3. **What is the problem?** — synthesize from body text across all items in cluster
4. **Current status** — most recent item's status field
5. **Plan / next steps** — extract action items from body text; if none found, mark ❓

Cite every field with source citation from `references/output-format.md`.

## Step 4 — Format output

For `daily_plan`:
```
## 🔴 TODAY — Must Do
<P0 clusters as item cards>

## 🟡 TODAY — Should Do
<P1 clusters as item cards>

## 🔵 THIS WEEK — Plan Ahead
<P2 clusters as single-line items with source>

## 📅 NEXT WEEK — Horizon
<P3 items>

## 🔔 REMINDERS & FOLLOW-UPS
<items with pending_response=true or upcoming_deadline>
```

For `incident_brief` and `meeting_brief`: use the formats defined in their respective skill files.

## Step 5 — Stale flags

For any item in `staleness_flags`: prepend ⚠️ STALE to its title in the output.
