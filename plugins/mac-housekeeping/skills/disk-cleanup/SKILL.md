---
name: disk-cleanup
description: Tiered, consent-gated Mac disk cleanup. Reclaims space by deleting safe caches (Go modcache, npm cache, ~/.cache, old Logs), project rebuild artifacts (scattered node_modules, .next, build, dist, target), and driving system/app cleanup (Docker prune + manual purge, brew cleanup). Each destructive step is gated behind explicit AskUserQuestion consent — never bulk-deletes on a verbal "ok". Encodes mac-specific gotchas (Docker.raw is sparse so prune doesn't shrink the file, MCP server node_modules power active tools, WhatsApp media must be cleaned from inside the app). Use whenever the user says "clean up my disk", "free up some space", "delete the caches", "run the cleanup", "nuke node_modules", or asks to act on a /disk-audit report. Trigger even if the user just says "do it" right after running /disk-audit. Never starts cold — ask the user to run /disk-audit first if you don't already have a report to act on.
allowed-tools: [bash, read]
model: sonnet
---

# Mac Disk Cleanup

Destructive companion to `/mac-housekeeping:disk-audit`. The audit produces a ranked report of reclaim candidates classified by reversibility; this skill walks the user through reclaiming each tier, with explicit per-action consent and mac-specific safety rails.

The whole skill is built around one rule: **the user authorises every destructive action by name, every time.** No batched "go ahead and clean everything" — that path leads to deleted work. The friction is the feature.

---

## Pre-flight: do we have an audit?

If the user hasn't run `/disk-audit` in this conversation, do that first. Don't try to clean blind — the audit is fast (under a minute) and the report is what tells you which `node_modules` directories actually exist on this machine, whether Docker is installed, which caches have meaningful size, etc.

If the user invokes this skill cold, say something like: "I need a recent audit to know what's worth cleaning. Want me to run `/mac-housekeeping:disk-audit` first?" and stop until they confirm.

If they push back ("just clean caches, I know they're big"), proceed with **only Tier 1** below — those locations are universal enough that you don't need a fresh audit to act on them safely. Skip Tier 2 and 3 without an audit.

---

## The auto-mode classifier reality

Claude Code's auto-mode safety classifier blocks "mass deletion of pre-existing files outside project scope" even when the user verbally consents in chat. This is correct behaviour — it stops a runaway agent from `rm -rf ~/Library` on a misread instruction — but it means *you cannot rely on `AskUserQuestion` consent alone* to authorise destructive `rm`/`find -delete` on system paths.

**Concrete consequence:** for any Tier 1 or Tier 2 deletion, expect the classifier to **block** even after the user picked "Yes, delete" in your question prompt. When that happens:

1. Don't retry the same command — the classifier will block again.
2. Don't try to bypass with workarounds (this is what the classifier is sniffing for).
3. **Print the exact command the user can paste into their own terminal**, in a code fence, and tell them you'll continue once they confirm they ran it.

This pattern — ask for consent → attempt → on classifier block, hand the command to the user — is the correct flow, not a fallback. Many commands in this skill will hit it.

---

## Tier 1 — Safe caches (always reclaimable, auto-rebuild)

These are the lowest-risk, highest-value targets. Group them into one consent prompt because they share a reversibility profile, and run them in whatever subset the user authorises.

```
Authorise which Tier 1 cleanups? (multi-select)
- go clean -modcache              (~13 GB, rebuilds on next go build)
- npm cache clean --force         (~2 GB, rebuilds on next npm install)
- yarn cache clean                (~1-2 GB, rebuilds on next yarn install)
- brew cleanup                    (~500 MB - 2 GB, removes old bottles)
- rm -rf ~/.cache/*               (~1-2 GB, tools regenerate)
- find ~/Library/Logs -mtime +30 -delete  (~500 MB, old app logs)
- empty Trash                     (whatever Trash currently holds)
```

Run each authorised step in its own Bash call (don't batch into one shell command — you want to see which ones the classifier blocks). When a step blocks, immediately print the manual command and move on to the next authorised step.

After Tier 1, run `df -h` and report what was reclaimed.

## Tier 2 — Project rebuild artifacts (reinstallable but takes time)

These need an audit to be actionable — the audit told you *which* `node_modules`/`.next`/`target`/etc. exist on this machine.

**Critical safety rule:** Before listing `node_modules` candidates for deletion, scan the candidate list for any path containing `mcp-servers`, `.claude`, or paths that look like active Claude Code tool dependencies (LSP servers, MCP server implementations). These power tools running *in the current Claude Code session*. Deleting them mid-session breaks the tools until reinstalled.

Present the list as two groups:

```
Project node_modules / build artifacts found:

Safe to delete (regular projects):
  ~/projects/foo/node_modules         (1.2 GB)
  ~/work/bar/.next                    (595 MB)
  ...

Powers active MCP/LSP tools — DELETION WILL BREAK CURRENT SESSION:
  ~/projects/claude-plugins/mcp-servers/php-lsp/node_modules
  ~/.claude/mcp-servers/ts-lsp/node_modules
  ...

Authorise which group(s) to delete?
- Delete safe group only (recommended)
- Delete safe group + MCP group (will need npm install in each MCP server before tools work again)
- Skip Tier 2
```

For each authorised path, run `rm -rf <path>` in its own Bash call. When the classifier blocks (it will, often), print the exact command in a code fence for the user to paste. Group blocked commands so the user can paste them all at once instead of one at a time:

```bash
rm -rf "~/projects/foo/node_modules" \
       "~/work/bar/.next" \
       ...
```

## Tier 3 — System and app cleanup (mac-specific gotchas)

This tier is where most automation goes wrong. Walk these one at a time.

### Docker

`docker system prune -a --volumes` reclaims space *inside* Docker's VM, but **does not shrink `Docker.raw` on the host filesystem.** `Docker.raw` is a sparse file — it grows but never auto-shrinks. So `prune` reports "5.6 GB reclaimed" while the host's free space goes up by 0.

Two-step recipe:

1. Ask consent for `docker system prune -a --volumes`. Run it. Note the reclaim figure.
2. **Tell the user explicitly** that the host won't see the reclaim until they purge the Docker disk image manually:
   - **Docker Desktop → Settings → Resources → "Clean / Purge Data"**
   - Or, for a full reset: **Settings → Troubleshoot → "Reset to factory defaults"** (nuclear; loses all images/volumes)

This is a UI step the user must do themselves. Don't try to manipulate `Docker.raw` from the shell — it can corrupt the Docker installation.

### Xcode (if installed)

`~/Library/Developer/Xcode/DerivedData` and `~/Library/Developer/Xcode/iOS DeviceSupport` accumulate quickly. Recommend `xcrun simctl delete unavailable` for old simulator runtimes, and offer to clear DerivedData. Always confirm Xcode is closed first — deleting DerivedData while a build is running can wedge Xcode.

### WhatsApp / Telegram / iMessage

Surface the size from the audit, but **do not touch the filesystem**. Deleting from `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared` corrupts WhatsApp's state. Direct the user to:

- **WhatsApp**: Settings → Storage and Data → Manage Storage
- **Telegram**: Settings → Data and Storage → Storage Usage
- **Messages.app**: Preferences → iMessage → enable "Messages in iCloud" + "Optimize Storage", or delete old conversations from inside Messages

### iOS device backups

`~/Library/Application Support/MobileSync/Backup/` holds full iPhone backups. Each can be 50+ GB. Don't auto-delete — these may be the user's only backup. Just surface the size and ask if they want to manage via Finder (cmd-click iPhone in sidebar → Manage Backups).

---

## Final reporting

After each tier, run `df -h` and report:

- Bytes free before / after this tier
- What was reclaimed
- What the user still needs to do manually (Docker Desktop purge, WhatsApp media, etc.)

End the session with a final summary:

```
Started: 8.8 GiB free
Ended:   31 GiB free
Reclaimed: ~22 GB

Outstanding manual steps:
- Docker Desktop → Settings → Resources → Clean / Purge Data (will free another ~5 GB)
- WhatsApp → Settings → Storage and Data (8 GB potential)
```

The point of the summary is to let the user close the loop — they should walk away knowing exactly what's still holding space and how to deal with it.

---

## What to never do in this skill

- Never run `rm -rf` or `find ... -delete` on system paths without explicit per-action AskUserQuestion consent.
- Never delete inside `~/Library/Group Containers/group.net.whatsapp.*`, `group.com.apple.messages.*`, or any `*.shared` group container — they're live app state, not caches.
- Never `rm` `Docker.raw` directly — corrupts the install. Use Docker Desktop's UI.
- Never delete `~/Library/Developer/CoreSimulator/Devices/*` while Xcode is running.
- Never delete `node_modules` under `~/.claude/`, `~/projects/claude-plugins/mcp-servers/`, or any path the user identifies as powering an active tool, without an explicit warning that the current Claude Code session's tools will break until reinstalled.
- Never use `sudo rm`. If a deletion needs sudo, hand the command to the user instead.
- Never empty Trash without explicit consent — the user may have files in there they meant to recover.
