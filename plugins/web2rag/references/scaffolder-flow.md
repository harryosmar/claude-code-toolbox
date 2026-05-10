# Scaffolder flow

How `/web2rag-init` produces a sibling project from this repo.

## Inputs

- `<name>` — project directory name
- `--target <dir>` — parent directory (default: parent of the plugin directory)

## Output path resolution

```
plugin_dir = <directory containing this plugin>
target_dir = --target  (or  parent(plugin_dir))
final_dir  = target_dir / <name>
```

Refuses if `final_dir` exists and is non-empty.

## Render rules

1. For every file under `templates/` (recursive):
   - If the path ends in `.tmpl`, run it through Jinja2 and write the result without the `.tmpl` suffix.
   - Otherwise copy verbatim.
2. Substituted variables:
   - `{{project_name}}` — `<name>`
   - `{{plugin_version}}` — read from `.claude-plugin/plugin.json`
   - `{{generated_at}}` — UTC ISO 8601 timestamp
3. After rendering, create empty `data/` and `reports/` directories (they're gitignored in the generated project, but need to exist so docker-compose volume mounts work on first boot).

## What the operator does next

1. `cd <final_dir>`
2. `/web2rag-setup` — fills `.env` (interactive, asks for `ANTHROPIC_API_KEY` + `CHAT_MODEL`)
3. `/web2rag-serve` — `docker compose up -d`
4. `/web2rag-ingest <url>` — first content load
5. `/web2rag-embed-snippet <site_id>` — print embed code

## What scaffolding NEVER does

- Touch any file outside `final_dir`.
- Run `git init` in `final_dir` (operator decides; some operators want to vendor inside an existing monorepo).
- Run `docker compose pull` or `up` (operator decides; first run can be slow due to the BGE-M3 + Prompt-Guard-2 image build).
- Read or write `.env` (that's `/web2rag-setup`'s job).
