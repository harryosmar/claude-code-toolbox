# MCP Server Setup Guide

Install and configure all MCP servers required by work-intel. Run these once on each host.

---

## 1. ChromaDB (`chroma-mcp`)

```bash
pip install chromadb chroma-mcp
```

Add to Claude Code MCP config (`~/.claude/mcp_servers.json`):
```json
{
  "chroma": {
    "command": "chroma-mcp",
    "args": ["--path", "${WORK_INTEL_HOME:-~/.work-intel}/chroma"]
  }
}
```

---

## 2. Jira (Atlassian Official MCP)

```bash
npm install -g @atlassian/atlassian-mcp-server
```

Add credentials to `~/.claude/secrets/jira.env`:
```bash
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@yourcompany.com
JIRA_API_TOKEN=your_api_token_here
```
Get token: https://id.atlassian.com/manage-profile/security/api-tokens

Add to MCP config:
```json
{
  "jira": {
    "command": "atlassian-mcp-server",
    "env": { "ATLASSIAN_TOKEN": "<your_token>", "ATLASSIAN_URL": "<your_url>" }
  }
}
```

---

## 3. Gmail (`@gongrzhe/server-gmail-autoauth-mcp`)

```bash
npm install -g @gongrzhe/server-gmail-autoauth-mcp
```

On first use, the server auto-opens OAuth browser flow. Credentials stored locally after first auth.

Add to MCP config:
```json
{
  "gmail": {
    "command": "server-gmail-autoauth-mcp"
  }
}
```

---

## 4. Google Workspace — Docs/Sheets/Slides (`aaronsb/google-workspace-mcp`)

```bash
git clone https://github.com/aaronsb/google-workspace-mcp ~/.mcp/google-workspace-mcp
cd ~/.mcp/google-workspace-mcp && npm install
```

Create OAuth credentials at https://console.cloud.google.com (Drive API + Docs API enabled).
Store at `~/.config/google-workspace-mcp/credentials.json`.

Add to MCP config:
```json
{
  "gdrive": {
    "command": "node",
    "args": ["${HOME}/.mcp/google-workspace-mcp/index.js"]
  }
}
```

---

## 5. WhatsApp / WAHA (`dudu1111685/waha-mcp`)

**Step 1 — Start WAHA Docker:**
```bash
docker run -d -p 3000:3000 --name waha devlikeapro/waha
```

**Step 2 — Link WhatsApp:** Open http://localhost:3000, scan QR with WhatsApp.

**Step 3 — Install MCP server:**
```bash
npm install -g waha-mcp
```

Add to MCP config:
```json
{
  "waha": {
    "command": "waha-mcp",
    "env": {
      "WAHA_URL": "http://localhost:3000",
      "WAHA_API_KEY": ""
    }
  }
}
```

---

## 6. Telegram (`sparfenyuk/mcp-telegram`)

```bash
pip install mcp-telegram
```

Requires Telegram API credentials (personal account, not a bot):
1. Go to https://my.telegram.org/apps
2. Create an app → get `api_id` and `api_hash`

Add to `~/.claude/secrets/telegram.env`:
```bash
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
```

Add to MCP config:
```json
{
  "telegram": {
    "command": "mcp-telegram",
    "env": {
      "TELEGRAM_API_ID": "<api_id>",
      "TELEGRAM_API_HASH": "<api_hash>"
    }
  }
}
```

---

## 7. Truewatch (already installed)

Already configured as `mcp__truewatch` — no additional setup needed.

---

## 8. Python Dependencies

```bash
pip install \
  sentence-transformers \
  chromadb \
  "unstructured[all-docs]" \
  ragas \
  langchain-anthropic \
  langchain-ollama \
  langchain-community
```

---

## 9. Ollama (optional — only for RAGAS evaluation with local LLM judge)

```bash
brew install ollama
ollama pull llama3.2
```

Only required if `config.json` has `ragas.llm_judge = "ollama"`.
