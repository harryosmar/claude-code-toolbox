#!/usr/bin/env python3
"""Scaffolder for /web2rag-init.

Copies templates/ into a sibling directory, rendering .tmpl files with simple
{{var}} substitution. No Jinja2 dependency — only flat variables are supported,
which is all the templates need.

Usage:
    python -m scripts.init <name> [--target <dir>] [--plugin-dir <dir>]

Refuses to overwrite an existing non-empty directory.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(text: str, vars: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in vars:
            raise KeyError(f"unknown template variable: {{{{{key}}}}}")
        return vars[key]

    return VAR_RE.sub(replace, text)


def load_plugin_version(plugin_dir: Path) -> str:
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text())["version"]


def scaffold(name: str, target: Path, plugin_dir: Path, *, force: bool = False) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ValueError(
            f"project name must match [a-z][a-z0-9_-]{{0,63}} (got {name!r}); "
            "lowercase letters, digits, hyphens, underscores only."
        )

    final = (target / name).resolve()
    if final.exists() and any(final.iterdir()):
        if not force:
            raise FileExistsError(
                f"target directory {final} already exists and is non-empty. "
                "Refusing to overwrite. Move/delete it manually, or pass --force."
            )

    templates_dir = plugin_dir / "templates"
    if not templates_dir.is_dir():
        raise FileNotFoundError(f"templates/ not found at {templates_dir}")

    vars = {
        "project_name": name,
        "plugin_version": load_plugin_version(plugin_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    final.mkdir(parents=True, exist_ok=True)
    files_written: list[Path] = []

    for src in templates_dir.rglob("*"):
        if src.is_dir():
            continue
        # Drop .gitkeep — they only exist so empty dirs survive in git.
        if src.name == ".gitkeep":
            continue

        rel = src.relative_to(templates_dir)
        # `.tmpl` files: render and strip the suffix.
        if rel.suffix == ".tmpl":
            rel = rel.with_suffix("")
            content = render(src.read_text(encoding="utf-8"), vars)
            dest = final / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        else:
            dest = final / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

        files_written.append(dest)

    # Empty volume mounts — must exist before docker compose up.
    (final / "data").mkdir(exist_ok=True)
    (final / "reports").mkdir(exist_ok=True)

    # Mark generation provenance.
    (final / ".web2rag-version").write_text(vars["plugin_version"] + "\n", encoding="utf-8")

    return final


def main() -> int:
    p = argparse.ArgumentParser(description="scaffold a new web2rag project")
    p.add_argument("name", help="project directory name (e.g. acme-bot)")
    p.add_argument(
        "--target",
        default=None,
        help="parent directory under which <name>/ is created (default: parent of the plugin dir)",
    )
    p.add_argument(
        "--plugin-dir",
        default=None,
        help="path to the web2rag plugin (default: parent of this script's directory)",
    )
    p.add_argument("--force", action="store_true", help="overwrite an existing non-empty target")
    args = p.parse_args()

    plugin_dir = Path(args.plugin_dir).resolve() if args.plugin_dir else Path(__file__).resolve().parent.parent
    target = Path(args.target).resolve() if args.target else plugin_dir.parent

    final = scaffold(args.name, target, plugin_dir, force=args.force)

    print(f"scaffolded: {final}")
    print()
    print("next steps:")
    print(f"  cd {final}")
    print("  /web2rag-setup        # fill .env interactively")
    print("  /web2rag-serve        # docker compose up -d")
    print("  /web2rag-ingest <url> # crawl + embed (zero cost)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
