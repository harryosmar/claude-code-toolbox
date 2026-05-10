# mac-housekeeping

Mac-only disk-space toolkit for the moment your drive is suddenly almost full and Finder's "Manage Storage" is lying to you.

## Skills

| Skill | What it does |
|---|---|
| `/disk-audit` | Read-only. Runs `df` + targeted `du` across the well-known macOS suspect locations (`Library/Caches`, `Library/Containers`, `Library/Group Containers`, `Library/Application Support`, `~/go`, scattered `node_modules`, etc.) and prints a ranked report with size, path, and reversibility class. |
| `/disk-cleanup` | Destructive companion. Walks the user through a three-tier reclaim flow (safe caches → project rebuild artifacts → system/app cleanup) with explicit per-action consent. Encodes mac-specific gotchas: Docker.raw is sparse so `prune` doesn't shrink it; WhatsApp media must be cleaned from inside the app; MCP server `node_modules` power active Claude Code tools and warn before deletion. |

## Why it exists

Finder under-reports because it doesn't know about developer state — Go module caches, Docker VM disk images, scattered `node_modules`, browser caches behind sandboxed Container permission walls, WhatsApp/Telegram media in Group Containers. This plugin encodes the shape of those hiding places so the audit and cleanup are fast and complete.

## Usage

Triggered conversationally. Phrases like "my mac is full", "running low on disk space", "what's eating my drive", "free up some space" should fire `/disk-audit` first; from there the user invokes `/disk-cleanup` to act on the report.

## What it deliberately does not do

- **Linux**: out of scope. Library/Caches/Group Containers/Docker.raw quirks are macOS-specific. A Linux equivalent would be a different plugin.
- **Auto-cleanup without consent**: every destructive step is gated. The auto-mode safety classifier may still block bulk `rm` even with consent — when that happens the skill hands the user the exact command to paste into their own terminal.
- **iOS device backups, Time Machine snapshots, iCloud Drive**: surfaced in the audit but never auto-deleted; these may be the user's only copy of something important.
