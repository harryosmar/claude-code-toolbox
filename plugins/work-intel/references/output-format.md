# Output Format Standards

## Emoji Status Indicators

| Emoji | Meaning |
|---|---|
| 🔴 | Critical / P0 / Must act now |
| 🟡 | Important / P1 / High priority |
| 🟢 | Resolved / Done / Healthy |
| 🔵 | Informational / This week |
| ⚠️ | Warning / Stale / Needs attention |
| ✅ | Completed / Verified |
| ❓ | Unknown / Missing information |
| 🔔 | Reminder / Follow-up needed |
| 📎 | Attachment / Document |

---

## Source Citation Format

Always cite the source of every item in brackets:

```
[Jira: PROJ-123]
[MR: !456]
[Alert: TW-20260506-001]
[Email: "Subject line here"]
[WA: Engineering Team • 2026-05-06 09:14]
[Telegram: Dev Updates • 2026-05-06 09:30]
[Drive: Q2 Roadmap.xlsx • sheet: Auth Service]
[Git: commit abc1234 • service-name]
[Truewatch: payment-service • 2026-05-06 08:55]
```

Always include timestamp for operational items (alerts, messages). Always include URL or ID for Jira/MR items.

---

## Confidence Level

When synthesizing across multiple sources, append a confidence marker:

- `[Confidence: High]` — 3+ sources corroborate
- `[Confidence: Medium]` — 2 sources or 1 authoritative source
- `[Confidence: Low]` — single indirect source or inference

---

## Item Card Format

Each item in a daily plan or report:

```
🔴 [PROJ-456] Auth service returning 500s on login
   Source: [Alert: TW-20260506-001] + [Jira: PROJ-456]
   Who: Reported by on-call (Alice), affects 12% of users
   Status: Investigating — last update 08:55
   Plan: Check recent deploys (commit abc1234 deployed 08:30)
   ETA: Unknown ❓
   [Confidence: High]
```

---

## Section Headers

```markdown
## 🔴 TODAY — Must Do
## 🟡 TODAY — Should Do  
## 🔵 THIS WEEK — Plan Ahead
## 📅 NEXT WEEK — Horizon
## 🔔 REMINDERS & FOLLOW-UPS
## ✅ RESOLVED SINCE LAST BRIEFING
```

---

## RAGAS Report Format

```
RAG Evaluation Report — 2026-05-06
===================================
Context Precision:   0.87  ✅ (+0.02 vs prior)
Context Recall:      0.79  ✅ (+0.01 vs prior)
Faithfulness:        0.91  ✅ (no change)
Answer Relevancy:    0.84  ⚠️ (-0.06 vs prior — REGRESSION)

Judge: ollama/llama3.2
Test set: 42 questions
```
