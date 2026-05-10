Release a new version of one of the plugins in this marketplace: validate, bump the named plugin's version, commit, refresh the marketplace, and update the local install.

This command is project-local — it only appears when Claude Code is launched from this personal multi-plugin marketplace repo. It is intended for the marketplace maintainer; it is not shipped to anyone via any plugin manifest.

**Arguments:** `$ARGUMENTS`
Expected format: `<plugin-name> <patch|minor|major> "<commit subject>"`

- `<plugin-name>` — must match a directory name under `plugins/`. Examples today: `web2rag`, `mac-housekeeping`, `work-intel`. Run `ls plugins/` to see what's available.
- `<patch|minor|major>`:
  - `patch` (e.g. `0.2.1` → `0.2.2`) — small fix, doc tweak, rule clarification.
  - `minor` (e.g. `0.2.1` → `0.3.0`) — new skill, new command, additive feature.
  - `major` (e.g. `0.2.1` → `1.0.0`) — breaking change to a skill or command interface.
- The commit subject is passed verbatim into `git commit -m`.

---

**Steps:**

### 1. Resolve repo and plugin paths

```bash
REPO=~/projects/claude-code-toolbox
PLUGIN_NAME="<plugin-name from args>"
PLUGIN_DIR="$REPO/plugins/$PLUGIN_NAME"
PLUGIN_JSON="$PLUGIN_DIR/.claude-plugin/plugin.json"
MARKETPLACE_NAME="claude-code-toolbox-marketplace"

# Sanity-check the plugin exists
test -f "$PLUGIN_JSON" || { echo "ERROR: $PLUGIN_JSON not found. Did you mistype the plugin name?"; exit 1; }
```

If the plugin directory or its manifest is missing, stop with a clear error. Don't proceed to bump anything.

### 2. Pre-flight: working tree must contain real edits inside the plugin

```bash
git -C "$REPO" status --porcelain -- "$PLUGIN_DIR"
```

The output MUST contain at least one modified or added file under `plugins/<plugin-name>/` **other than** `.claude-plugin/plugin.json`. If only the manifest is dirty — or nothing under the plugin dir is dirty at all — stop and warn the user: they probably forgot to edit a skill or command, or they typed the wrong plugin name. A no-op version bump is almost never what someone wants.

(Edits outside `plugins/<plugin-name>/` — e.g. to the top-level README, the marketplace.json, or another plugin's files — are fine to coexist and will be staged in the same commit if they relate to this release. Up to your judgment.)

If `$REPO` is not a git repository yet, stop with the message: `"$REPO is not a git repo — initialise it first with 'git -C $REPO init && git -C $REPO add . && git -C $REPO commit -m initial' before running release-claude-code-toolbox"`. Don't try to init for them; release flow assumes a working remote.

### 3. Read the current version

```bash
python3 -c "
import json
v = json.load(open('$PLUGIN_JSON'))['version']
print('current_version:', v)
"
```

### 4. Compute the new version from the bump arg

For `<patch|minor|major>`, increment the appropriate field and reset lower fields to 0:

| Bump | `0.2.1` becomes |
|---|---|
| patch | `0.2.2` |
| minor | `0.3.0` |
| major | `1.0.0` |

If the bump arg is anything else, stop with an error.

### 5. Edit `plugin.json` to the new version

Use the **Edit** tool on `<PLUGIN_JSON>`. Replace the single `"version": "<old>",` line with `"version": "<new>",`. Do not touch any other field.

### 6. (No README version table here)

Unlike some marketplaces, this repo's `README.md` lists plugins as bullets without per-plugin version cells, so there is no table row to update. If you (the maintainer) later add a versioned plugins table to the README, mirror the regex-replace approach from `release-peruri-plugin` in the team marketplace. For now, skip and proceed to validation.

### 7. Validate the marketplace

```bash
claude plugin validate "$REPO"
```

If validation fails, **revert the version bump** (Edit it back to the previous value) and stop with the validator's error message. Do not commit a manifest the validator rejects.

### 8. Show the user what is about to be committed

```bash
echo "=== status ==="
git -C "$REPO" status
echo ""
echo "=== diff --stat ==="
git -C "$REPO" diff --stat
```

### 9. Confirm before staging and committing

Ask the user explicitly: *"Bump `<plugin-name>` to `<new>` and commit with message: `<commit subject>` ? (yes/no)"*

- If the user replies `yes` (or any clear affirmative — `y`, `ok`, `go`), continue.
- If the user replies anything other than a clear yes (or asks a question), **revert the version bump** with another Edit on `plugin.json`, leave the rest of their working tree untouched, and stop.

### 10. Stage an explicit file list and commit

```bash
# Pass an explicit space-separated list of modified files; never `git add -A` or `git add .`.
git -C "$REPO" add plugins/$PLUGIN_NAME/.claude-plugin/plugin.json <other-modified-files>

git -C "$REPO" commit -m "<plugin-name>: <commit subject> (<new-version>)

<optional body — leave empty if the user didn't provide one>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
"
```

Prefixing the commit subject with `<plugin-name>:` makes `git log --oneline` readable when multiple plugins live in the same repo.

If the commit fails (e.g. pre-commit hook), surface the error and stop. Do not retry with `--no-verify`. Do not amend a previous commit; create a new one if the user fixes the underlying issue.

### 11. Push to origin

```bash
git -C "$REPO" push
```

If the push fails (e.g. remote has diverged), surface the error and stop. Do not force-push. If there is no `origin` remote configured, surface that and stop — the maintainer can push later by hand once they've pointed the repo at one.

### 12. Refresh the local marketplace from this source

```bash
claude plugin marketplace update "$MARKETPLACE_NAME"
```

### 13. Update the installed plugin to the new version

```bash
claude plugin update "$PLUGIN_NAME@$MARKETPLACE_NAME"
```

The output should say `Plugin "<plugin-name>" updated from <old> to <new> for scope user. Restart to apply changes.` If it says "already up to date", something went wrong with the marketplace refresh — stop and ask the user to investigate.

### 14. Print a release summary

```
✓ Released <plugin-name> <old> → <new>
   Commit:  <git rev-parse --short HEAD output>
   Restart: /exit and re-launch Claude Code to load the new skill bodies into memory.
```

---

**Critical rules:**

- **Always operate on the named plugin only.** If the user passes `web2rag`, only `plugins/web2rag/.claude-plugin/plugin.json` is bumped. Don't touch other plugins' versions.
- **Never use `git add -A` or `git add .`.** Always pass an explicit file list. Multiple plugins coexist in this repo, so a wide stage could pull in unrelated work — including local-only workspace data that should stay out of git (the `.gitignore` covers `plugins/*-workspace/` and `plugins/*/skills/*-workspace/`, but explicit staging is still safer).
- **Never use `--no-verify`, `--no-gpg-sign`, or any hook-bypass flag.** If a pre-commit hook fails, the answer is to fix the issue, not skip the check.
- **Never force-push.** Step 11 does a plain `git push`; if the push fails due to a diverged remote, surface the error and stop — do not use `--force` or `--force-with-lease`.
- **Never amend a prior commit.** Each release is a new commit on top.
- **If the user declines the confirmation in step 9, revert the version bump on disk** so the working tree is exactly as they left it before invoking `/release-claude-code-toolbox`. Their other edits stay; only `plugins/<plugin-name>/.claude-plugin/plugin.json` returns to the prior value.
- **Stop on validator errors.** A manifest the validator rejects will fail to install, and committing it just creates noise.
- **Do not prune old cache versions** under `~/.claude/plugins/cache/...`. They are harmless; `claude plugin marketplace update` handles cache management.
- **Do not auto-tag releases** (`git tag v<new>`). The `claude plugin tag` subcommand is available if the user wants tags later, but tagging is out of scope here.
