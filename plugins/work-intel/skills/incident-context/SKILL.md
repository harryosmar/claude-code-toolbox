---
name: incident-context
description: Fast root-cause context for an alert, incident, or production issue. Searches ChromaDB for past incidents, Jira tickets, war room messages, and recent deployments, then answers the 5-question intelligence framework (What / Who / Problem / Status / Plan). Also fetches live alert details from Truewatch when an alert ID or service name is given. Use this skill whenever: a P1 alert fires, production is degraded, you need context before a war room, or the user asks "what's going on with [service/incident]", "explain this incident", "root cause for [X]", "what happened with [Y]", "P1 brief", "incident brief", "war room context". Trigger even if the user just describes symptoms like "login is failing" or "users can't access the portal".
allowed-tools: [bash, read, mcp__truewatch__truewatch_query, mcp__truewatch__truewatch_ping]
model: sonnet
---

# Incident Context

Produce a fast root-cause brief for an active or recent incident.

The guiding principle: the user is in or about to enter a war room. They need *now* — what broke, who's involved, what was deployed recently, and what the team already tried. Speed and citation accuracy matter more than completeness.

## Hard Rules

- Answer all 5 questions from `references/intelligence-framework.md`. Never leave one blank — use ❓ Unknown if data is missing.
- Cite every claim. Every item must have a source citation on its own line in this exact format:
  ```
  Source: [WA: <group name> • <YYYY-MM-DD HH:MM>]
  Source: [Jira: PROJ-123]
  Source: [Alert: TW-20260506-001]
  Source: [MR: !456 • service-name]
  ```
  Never write "from WhatsApp" or "mentioned in messages" — always the bracketed form.
- Do not speculate without stating confidence level (High/Medium/Low).
- Prioritize recent data (last 48h) over older context for the incident itself.
- If Truewatch returns no data, continue with ChromaDB — don't stop.

## Input

Accept any of:
- A Truewatch alert ID (e.g. `TW-20260506-001`)
- A service name (e.g. `payment-service`, `portal-informasi-fe-sipgn`)
- A free-text description (e.g. "login is failing for some users", "webchat incident on portal")

## Step 0 — Tier A input guard

Read `references/security-guards.md` and spawn `guard-input-agent` with the user's incident description / service name / alert ID as `query`. On `breached: true`, stop with the security banner. Skip if `WORK_INTEL_GUARDS_QUERY=disabled` or `guards.query.enabled: false`.

## Step 1 — Query ChromaDB first (fast, local)

ChromaDB already holds war room messages, Jira history, and past incidents. Query it before any network call.

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "<incident description or service name>" \
  --top-k 20 \
  --output json

python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "<incident description> incident war room action blocked" \
  --top-k 10 \
  --output json
```

Deduplicate by `base_id`. Note the `timestamp` of the most recent relevant item.

## Step 2 — Check recent deployments

Flag anything deployed within 2 hours before the incident started — classic change-incident correlation.

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --q "deploy commit release merge <service name>" \
  --since 48h \
  --source gitlab \
  --top-k 5 \
  --output json
```

## Step 3 — Fetch Truewatch alert (if alert ID or service given)

If the user provided an alert ID or service name, use the Truewatch MCP tool to get live alert details:
- Use `mcp__truewatch__truewatch_query` with the service name or alert ID
- Extract: service, metric breached, threshold, triggered_at, current status, severity

If no Truewatch data found, note it and continue with ChromaDB results.

## Step 4 — Similarity search for past incidents

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/query_chroma.py" \
  --similar-to "<alert_id_or_incident_description>" \
  --top-k 5 \
  --source truewatch,jira \
  --output json
```

Look for a past incident that was resolved — the resolution steps are the most valuable part.

## Step 5 — Answer the 5 questions

Compose the brief (do NOT display yet — Step 6 guards it).

Using all gathered data:

```
## Incident Context Brief — <service/description> — <date>

### 1. What's going on?
<1-3 sentence summary>  [Confidence: High/Medium/Low]
Source: [...]

### 2. Who is involved?
Reporter: ... | Assignee: ... | Affected users/scope: ...
Source: [...]

### 3. What is the problem?
Root cause hypothesis: ...
Symptoms: ...
Impact scope: ...
Source: [...]

### 4. Current status
Last known action: ... at <time>
Active alerts: ...
Blockers: ...
Source: [...]

### 5. Plan / next steps
[ ] <action 1> — owner: <name>
[ ] <action 2> — owner: <name>
Related past incident: [if found] <title> — resolved by <action>
Source: [...]

---
⚠️ Unknown: <anything that could not be determined>
⚠️ Failed sources (using cached data): <list or "none">
```

## Step 6 — Tier A output guard

Read `references/security-guards.md`. Spawn `guard-output-agent` with the assembled brief markdown as `markdown` and the deduped retrieved items' `base_id` + `source` + `title` as `contexts`. Apply the response per the reference:
- `breached: true` → display the security block banner instead of the brief.
- `breached: false` + redactions empty → display the brief as-is.
- `breached: false` + redactions present → display `redacted_text` followed by `[SECURITY] N items redacted: ...` footer.

Skip if `WORK_INTEL_GUARDS_OUTPUT=disabled` or `guards.output.enabled: false`.
