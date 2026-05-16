"""Shared LLM judge factory for batch eval (RAGAS + security-audit).

Used by ragas_eval.py and security_audit.py. NOT used by inline guards —
inline query/output guards run as Claude Code subagents (Tier A) and inline
ingest guards run as a local Presidio classifier (Tier B). This module is
Tier C only.

Reads judge config from `judge.*` (preferred) or legacy `ragas.*` fields
for backward compat. Supports remote-host Ollama via `ollama_base_url`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _judge_block(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the effective judge config, merging top-level `judge` over legacy `ragas.*`."""
    legacy = cfg.get("ragas", {}) or {}
    legacy_mapped = {
        "provider": legacy.get("llm_judge"),
        "anthropic_model": legacy.get("anthropic_model"),
        "ollama_model": legacy.get("ollama_model"),
        "ollama_base_url": legacy.get("ollama_base_url"),
    }
    judge = cfg.get("judge", {}) or {}
    return {k: judge.get(k, legacy_mapped.get(k)) for k in
            ("provider", "anthropic_model", "ollama_model", "ollama_base_url")}


def get_judge(cfg: dict[str, Any]) -> tuple[Any, str]:
    """Build a LangChain chat model from config.

    Returns (model, label) where label is "<provider>/<model>" for report headers.
    """
    j = _judge_block(cfg)
    provider = j.get("provider") or "ollama"

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        model = j.get("anthropic_model") or "claude-haiku-4-5-20251001"
        return ChatAnthropic(model=model), f"anthropic/{model}"  # type: ignore[call-arg]

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        model = j.get("ollama_model") or "llama3.2"
        base_url = j.get("ollama_base_url")
        kwargs: dict[str, Any] = {"model": model}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOllama(**kwargs), f"ollama/{model}@{base_url or 'localhost'}"

    raise ValueError(f"Unknown judge.provider: {provider!r}. Expected 'anthropic' or 'ollama'.")


def health_check(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Probe the configured backend without making a real LLM call.

    Anthropic: just confirms ANTHROPIC_API_KEY is set.
    Ollama: GETs <base_url>/api/tags.
    Returns (ok, message).
    """
    j = _judge_block(cfg)
    provider = j.get("provider") or "ollama"

    if provider == "anthropic":
        if os.environ.get("ANTHROPIC_API_KEY"):
            return True, "ANTHROPIC_API_KEY present"
        return False, "ANTHROPIC_API_KEY not set in environment"

    if provider == "ollama":
        base_url = j.get("ollama_base_url") or "http://localhost:11434"
        url = base_url.rstrip("/") + "/api/tags"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"unsafe scheme {parsed.scheme!r} for ollama base_url (only http/https are allowed)"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
                if resp.status == 200:
                    return True, f"ollama reachable at {base_url}"
                return False, f"ollama returned HTTP {resp.status} at {url}"
        except (urllib.error.URLError, OSError) as e:
            return False, f"ollama unreachable at {url}: {e}"

    return False, f"unknown provider {provider!r}"


def _load_config(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ Malformed config {path}: {e}", file=sys.stderr)
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge factory CLI (health-check only).")
    parser.add_argument("--config", default=os.path.join(
        os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel")),
        "config.json"))
    parser.add_argument("--health-check", action="store_true",
                        help="Probe the configured judge backend; exit 0 on success.")
    args = parser.parse_args()

    cfg = _load_config(args.config)

    if args.health_check:
        ok, msg = health_check(cfg)
        icon = "✅" if ok else "❌"
        print(f"{icon} judge health: {msg}")
        sys.exit(0 if ok else 1)

    # Default: print the resolved judge label without instantiating
    j = _judge_block(cfg)
    print(json.dumps(j, indent=2))


if __name__ == "__main__":
    main()
