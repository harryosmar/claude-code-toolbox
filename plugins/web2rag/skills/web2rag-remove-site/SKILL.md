---
name: web2rag-remove-site
description: Permanently delete a previously-ingested site from a generated web2rag project's vector store. Use whenever the user wants to remove, drop, delete, purge, or uninstall a site from the chatbot's knowledge base — phrasings like "remove docs.acme.com", "drop this site from the bot", "purge the old docs site", "hapus site X" (id), "uninstall this knowledge". Also trigger when the user is migrating from an old domain ("we moved from old.acme.com to docs.acme.com — drop the old one"). Do NOT use to refresh a site's content (that's web2rag-update) or to stop the stack (that's web2rag-stop).
allowed-tools: [bash, read]
---

# web2rag-remove-site

Delete every chunk for one site.

## Inputs

- `<site_id>` — site identifier as listed by `/web2rag-list-sites`
- `--project <path>` — target project (default cwd)

## What this skill does

1. Confirms with the operator: prints the page + chunk count for the site, asks "delete? (y/N)".
2. On confirmation, DELETEs `/sites/<site_id>` on the api. The api translates this into `chroma.delete(where={site_id: ...})`.
3. Asserts a follow-up GET `/sites` no longer lists the site.
4. Prints "removed N pages / M chunks for <site_id>".

## Inviolable rules

- Never skip the confirmation prompt — there is no `--yes` shortcut. Re-ingesting is cheap, but accidentally nuking a customer's curated corpus is expensive.
