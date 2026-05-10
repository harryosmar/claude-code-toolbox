"""Full historical fetch for a single source during work-intel setup (init mode)."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return json.load(f)


def load_offsets() -> dict:
    path = os.path.join(WORK_INTEL_HOME, "offsets.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def write_offset(source: str, value: dict) -> None:
    offsets = load_offsets()
    offsets[source] = value
    path = os.path.join(WORK_INTEL_HOME, "offsets.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(offsets, f, indent=2)
    os.replace(tmp, path)


def embed_batch(items: list) -> int:
    if not items:
        return 0
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "embed_item.py"), "--collection", "items"],
        input=json.dumps(items).encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr.decode(), file=sys.stderr)
    for line in result.stdout.decode().splitlines():
        if line.startswith("ITEMS_EMBEDDED:"):
            return int(line.split(":")[1].strip())
    return len(items)


def init_truewatch(cfg: dict, hours: int = 72) -> int:
    # Truewatch init: query last N hours via MCP (called by the agent using MCP tools)
    # This script sets the offset; actual fetching is done by the agent.
    now = datetime.now(timezone.utc).isoformat()
    write_offset("truewatch", {"last_alert_ts": now})
    print(f"✅ truewatch: offset set to {now} (MCP query will run on first briefing)")
    return 0


def init_gdrive(cfg: dict) -> int:
    # Google Drive init: set a placeholder offset; actual token fetched via MCP.
    # The agent will call the Drive changes.list API to get the real pageToken.
    write_offset("gdrive", {"page_token": "INIT_PENDING"})
    print("✅ gdrive: offset placeholder set — real token will be fetched on first briefing via MCP")
    return 0


def init_generic_placeholder(source: str, offset_value: dict) -> int:
    """For sources where init requires MCP calls (done by the agent, not this script)."""
    write_offset(source, offset_value)
    print(f"✅ {source}: offset placeholder set — full historical fetch will run via agent")
    return 0


def init_local_folder(cfg: dict) -> int:
    """Run a full scan of every configured watched folder, ingesting all files as NEW.

    Reuses sync_folder.sync_all — on a clean offsets.json the per-label diff classifies
    every file as NEW, so this naturally becomes a one-shot bulk ingest, and the offset
    is left in delta-ready state for subsequent runs.
    """
    watches = (cfg.get("local_folders") or {}).get("watches") or []
    if not watches:
        print("✅ local_folder: no watched folders configured — nothing to ingest")
        # Still write a placeholder offset so subsequent setup runs skip this source
        write_offset("local_folder", {"files": {}, "last_scan_ts": "INIT_PENDING"})
        return 0

    sys.path.insert(0, SCRIPTS_DIR)
    from sync_folder import sync_all  # type: ignore[import-not-found]

    result = sync_all()
    if result.get("error"):
        print(f"⚠️  local_folder: {result['error']}")
        return 0

    total = 0
    for s in result["summaries"]:
        errs = len(s.get("errors") or [])
        suffix = f" ({errs} errors)" if errs else ""
        print(f"  📂 {s['label']}: {s['new']} files ingested{suffix}")
        total += s["new"]
    return total


SOURCE_HANDLERS = {
    "truewatch":    lambda cfg: init_truewatch(cfg),
    "gdrive":       lambda cfg: init_gdrive(cfg),
    "local_folder": lambda cfg: init_local_folder(cfg),
    # jira, gitlab, gmail, waha, telegram: handled by the agent using MCP tools.
    # These placeholders allow the agent to know init is in progress.
    "jira":     lambda cfg: init_generic_placeholder("jira",     {"updated_after": "INIT_PENDING"}),
    "gitlab":   lambda cfg: init_generic_placeholder("gitlab",   {"since": "INIT_PENDING", "last_event_id": 0}),
    "gmail":    lambda cfg: init_generic_placeholder("gmail",    {"history_id": "INIT_PENDING"}),
    "waha":     lambda cfg: init_generic_placeholder("waha",     {"last_message_timestamp": 0}),
    "telegram": lambda cfg: init_generic_placeholder("telegram", {"last_message_id": 0}),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=list(SOURCE_HANDLERS.keys()))
    parser.add_argument("--config", default=os.path.join(WORK_INTEL_HOME, "config.json"))
    args = parser.parse_args()

    cfg = load_config(args.config)
    count = SOURCE_HANDLERS[args.source](cfg)
    print(f"init_source: {args.source} ready ({count} items embedded by script; agent handles MCP-based fetch)")


if __name__ == "__main__":
    main()
