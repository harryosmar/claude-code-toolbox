---
name: web2rag-embed-snippet
description: Print the ready-to-paste HTML embed snippet for a generated web2rag project's chat widget — a single `<script>` tag that the customer drops into their website to mount the bilingual chatbot. The snippet pre-fills the project's api URL and site_id; the customer can override theme / accent / launcher position via data-* attributes. Use whenever the user wants the embed code for a customer's site — phrasings like "embed snippet for docs.acme.com", "give me the embed code", "what script tag do i paste on the website", "kasih snippet buat di-paste ke website client" (id), "how does the customer embed this?". Do NOT use to scaffold a project (web2rag-init), to ingest a site (web2rag-ingest), or to start the stack (web2rag-serve).
allowed-tools: [bash, read]
---

# web2rag-embed-snippet

Print the customer-facing embed snippet.

## Inputs

- `<site_id>` — site identifier as listed by `/web2rag-list-sites`
- `--api-url <url>` — public URL where the api is reachable (default: `http://localhost:8787`, but production deployments override this)
- `--theme auto|light|dark` (default `auto`)
- `--accent <color>` (default `#6B5BFF`)
- `--position bottom-right|bottom-left` (default `bottom-right`)
- `--locale auto|en|id` (default `auto` — locale resolves at runtime via navigator.language)
- `--project <path>` — target project (default cwd)

## What this skill does

1. Asserts the site exists in the project's vector store.
2. Renders the snippet template with the inputs substituted.
3. Prints the snippet AND a one-line "paste this into the customer's HTML before `</body>`" instruction.
4. Also prints `<api-url>/demo?site_id=<site_id>` — an operator-facing demo
   page served by the api itself (mounts the same widget against a sample
   landing page). Use it to smoke-test the chat in a browser before handing
   the embed snippet to a customer; defaults to the first ingested site
   when no site_id is passed.

## Output shape

```html
<script
  src="http://localhost:8787/widget.js"
  data-api-url="http://localhost:8787/chat"
  data-site-id="docs.acme.com"
  data-theme="auto"
  data-accent-color="#6B5BFF"
  data-launcher-position="bottom-right"
  data-locale="auto"
></script>
```
