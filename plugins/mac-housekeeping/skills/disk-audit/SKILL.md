---
name: disk-audit
description: Read-only Mac disk-usage audit. Runs df + targeted du across the well-known macOS suspect locations (Library/Caches, Library/Containers, Library/Group Containers, Library/Application Support, ~/go, ~/.npm, ~/.cache, scattered node_modules / .next / target / dist / build) and produces a ranked report with size, path, and reversibility class. Never deletes anything. Use whenever the user says "my mac is full", "disk almost out", "running low on space", "what's eating my disk", "where did my disk space go", "find big folders on my mac", "audit disk usage", "check disk space" — or just sounds frustrated about their drive being full. Trigger even if the user only describes symptoms ("xcode won't open, says no space") rather than naming the audit. Pairs with /disk-cleanup for the destructive follow-up.
allowed-tools: [bash, read]
model: sonnet
---

# Mac Disk Audit

A read-only audit. Every command in this skill either reads disk metadata (`df`, `du`, `find`, `ls`) or prints. **No file is created, modified, or deleted.** That separation is the point — running this skill must be safe even when the user is panicking about lost work.

If the user wants to actually free space afterwards, they invoke `/mac-housekeeping:disk-cleanup`. Don't preempt that step from here.

---

## Why this skill exists

When a Mac fills up, the obvious tools (Finder's "Manage Storage", Disk Utility) usually under-report because they don't know about developer state: Go module caches, Docker VM disk images, scattered `node_modules`, browser cache directories that hide behind Container permission walls, WhatsApp/Telegram media in `Group Containers`. This skill encodes the shape of those hiding places so the audit is fast and complete instead of a slow `du -sh /` that takes hours.

## Workflow

### Step 1 — Snapshot total usage

Run `df -h` first. Two numbers matter:

- **`Avail`** — free space. If under ~20 GiB on a 250 GiB drive, treat as urgent.
- **`Capacity`** — % used. >90% is the threshold where macOS itself starts misbehaving (Spotlight rebuilds, Time Machine pauses, swap grows).

Quote both numbers back to the user up front so they know how much they're trying to claw back.

### Step 2 — Drill into the home directory (top level)

```bash
du -sh /Users/<user>/* 2>/dev/null | sort -rh | head -20
```

`2>/dev/null` is important — Library has many permission-restricted files and the noise drowns the signal otherwise. Top suspects you'll usually see ranked:

- `~/Library` — typically 30–80 GB on a working machine
- `~/go` — Go workspace, often 10–30 GB on a Go developer's machine
- `~/Downloads` — abandoned installers and zips
- `~/Documents`, `~/Desktop` — user files
- Any project-folder root the user keeps at home (`~/projects`, `~/work`, etc.)

### Step 3 — Drill into ~/Library

`~/Library` is opaque from Finder. Always probe these four subdirs:

```bash
du -sh ~/Library/Caches/* 2>/dev/null | sort -rh | head -15
du -sh ~/Library/Containers/* 2>/dev/null | sort -rh | head -10
du -sh ~/Library/Group\ Containers/* 2>/dev/null | sort -rh | head -10
du -sh "~/Library/Application Support"/* 2>/dev/null | sort -rh | head -10
```

**Surprise:** the parent's `du -sh` total often exceeds the visible children's sum because some subdirs are sandboxed and `du` can't enter them as the user. Don't be alarmed — that's normal. If you need to confirm a hidden bulk, `sudo du -sh <path>` resolves it but ask before running sudo.

**Things to flag specifically when you see them:**

- `~/Library/Containers/com.docker.docker` — Docker Desktop's VM disk lives here. Look for `Data/vms/0/data/Docker.raw`. It's a **sparse file**: `ls -lah` shows logical size (often 32–64 GB), `du -sh` shows real usage. Note both numbers in the report — the user will need them later.
- `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared` — WhatsApp media archive. Frequently 5–15 GB. Cannot be cleaned from outside the app (deleting the directory corrupts WhatsApp state). Flag it but mark "manual via app".
- `~/Library/Caches/Yarn` — historical Yarn cache, often forgotten.
- `~/Library/Caches/Homebrew` — old downloaded bottles, safe to clear with `brew cleanup`.

### Step 4 — Drill into the dev workspace

If `~/go` (or `~/code`, `~/projects`, whatever the user's convention is) showed up in step 2, break it down:

```bash
du -sh ~/go/pkg ~/go/src ~/go/bin 2>/dev/null
```

`~/go/pkg` is the Go module cache — **always 100% reclaimable** (`go clean -modcache`).

`~/go/src` usually contains node_modules sprawl from JS/TS sub-projects. Find the offenders:

```bash
find ~/go/src ~/projects ~/work -type d \( -name node_modules -o -name .next -o -name .venv -o -name venv -o -name target -o -name build -o -name dist \) -prune 2>/dev/null \
  | xargs -I{} du -sh {} 2>/dev/null | sort -rh | head -20
```

(Adjust the path roots to wherever the user keeps code — read the home-directory listing from step 2 to pick.)

### Step 5 — Probe the misc dotfile caches

```bash
du -sh ~/.npm ~/.cache ~/.gradle ~/.m2 ~/Library/Logs ~/.Trash 2>/dev/null
```

These are quiet space hogs on long-lived machines. `~/.Trash` is a special call-out — it counts toward "used" until emptied.

### Step 6 — Produce the report

Format the findings as a markdown table with one row per significant finding (>500 MB), sorted descending by size. Use this exact column set so the cleanup skill can read it:

| Size | Path | Reversibility | Notes |
|---|---|---|---|
| 13 GB | `~/go/pkg/mod` | safe-cache | `go clean -modcache` rebuilds on next build |
| 16 GB | `Docker.raw` (32 GB allocated) | system-app | Need Docker Desktop "Clean / Purge Data" to actually shrink the file |
| 8.3 GB | `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared` | app-manual | Clean from inside WhatsApp; deleting directly corrupts state |
| 1.2 GB | `~/go/src/.../node_modules` (×N) | project-rebuild | `npm/yarn install` re-creates |

**Reversibility classes — use these labels exactly:**

- `safe-cache` — auto-rebuilds on next use, no user action needed afterwards. Examples: Go modcache, npm cache, .cache, Library/Logs >30d, Homebrew cache, Yarn cache.
- `project-rebuild` — reinstallable but requires explicit user action (e.g. `npm install`) and may take time / hit a registry. Examples: scattered `node_modules`, `.next`, `target`, `dist`, `build`.
- `system-app` — removable but with mac-specific quirks the user must understand. Examples: Docker.raw (sparse-file behaviour), Xcode DerivedData (reset Xcode state).
- `app-manual` — must be done from inside the app's UI; do not delete from the filesystem. Examples: WhatsApp/Telegram media, iMessage attachments.
- `user-data` — actual user files that may matter. Never auto-recommend deletion. Examples: `~/Downloads`, `~/Documents`. Surface size only.

End the report with two lines:

```
Total reclaimable (safe + project + system): ~XX GB
Suggested next step: /mac-housekeeping:disk-cleanup
```

Keep the report compact. The user's time is the bottleneck, not yours — they need to scan it in 30 seconds and decide.

---

## What to never do in this skill

- Don't run `rm`, `find ... -delete`, `docker prune`, or anything that mutates state.
- Don't run `sudo` without asking; the audit works fine without it for the rough picture.
- Don't recurse with `du` over the full home directory at top level (`du -sh ~`) — slow and pointless. The drill-down sequence above is faster and more informative.
- Don't suggest specific deletions in the report body. Reversibility class + size is enough; the user (or `/disk-cleanup`) decides what goes.
- Don't read or quote contents of any user file. Filenames and sizes only.
