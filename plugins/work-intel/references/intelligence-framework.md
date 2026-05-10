# Intelligence Framework

## The 5-Question Model

For every incident, task cluster, or knowledge case, always answer all five questions. Never leave one unanswered — if information is missing, state explicitly what is unknown and flag it for follow-up.

1. **What's going on?** — Concise situational summary (1–3 sentences)
2. **Who is involved?** — Requester, reporter, affected parties, assignee, stakeholders
3. **What is the problem?** — Root cause hypothesis, symptoms, impact scope
4. **What is the progress status?** — Current state, last known action, blockers
5. **What is the plan / decision made?** — Next steps, decisions recorded, ETA, owner

---

## Priority Scoring (P0–P3)

| Priority | Label | Criteria | SLA for update |
|---|---|---|---|
| P0 | Critical | Active incident, SLA breach, production down, client-impacting, blocked teammate | Flag if no update in 24h |
| P1 | High | Sprint commitment, escalated ticket, Truewatch alert in warning state, merge blocked | Flag if no update in 72h |
| P2 | Medium | Planned task, documentation, non-blocking improvement | No staleness alert |
| P3 | Low | Nice-to-have, future research, backlog grooming | No staleness alert |

---

## Priority Assignment Rules

When classifying an ingested item, assign priority based on the first matching rule:

- Any Truewatch alert with severity `critical` → **P0**
- Any Truewatch alert with severity `warning` → **P1**
- Any Jira ticket with `priority=Blocker` or `priority=Critical` and status not `Done` → **P0**
- Any Jira ticket with `priority=High` and status `In Progress` → **P1**
- Any GitLab pipeline failure on a protected branch → **P1**
- Any MR open > 3 days awaiting review → **P2**
- Any WA/Telegram/email message where YOU are @mentioned by name or @lid → **P1** (someone is directly asking for your attention)
- Any email/WA/Telegram message explicitly mentioning "urgent", "ASAP", "P1", "down", "blocked" → **P1**
- Default → **P2**

---

## Staleness Detection

Run on every `morning-briefing` call:

- P0 items with no ChromaDB update in last 24 hours → prepend ⚠️ STALE to title
- P1 items with no ChromaDB update in last 72 hours → prepend ⚠️ STALE to title
- Items with `status=resolved` or `status=closed` → exclude from daily plan

---

## Daily Plan Structure

```
🔴 TODAY — Must Do (P0 / Critical / Urgent)
   Top 3–5 items. Include: item name, source citation, why critical, who is waiting, ETA.

🟡 TODAY — Should Do (P1 / Important)
   High-value items with some flexibility.

🔵 THIS WEEK — Plan Ahead (P2 with upcoming deadline)
   Items with deadlines within 7 days or cross-team dependencies.

📅 NEXT WEEK — Horizon (P3 / strategic)
   Preparatory items to keep in view.

🔔 REMINDERS & FOLLOW-UPS
   Pending responses awaited, SLAs expiring, scheduled reviews, commitments made.
```
