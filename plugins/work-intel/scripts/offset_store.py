"""Read and write per-source delta offsets and config values for work-intel."""
import argparse
import json
import os
import sys

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
OFFSETS_PATH = os.path.join(WORK_INTEL_HOME, "offsets.json")
CONFIG_PATH = os.path.join(WORK_INTEL_HOME, "config.json")


def read_offsets():
    if not os.path.exists(OFFSETS_PATH):
        return {}
    with open(OFFSETS_PATH) as f:
        return json.load(f)


def write_offsets(offsets: dict):
    os.makedirs(WORK_INTEL_HOME, exist_ok=True)
    tmp = OFFSETS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(offsets, f, indent=2)
    os.replace(tmp, OFFSETS_PATH)


def read_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_nested(obj: dict, key_path: str):  # type: ignore[type-arg]
    keys = key_path.split(".")
    current: object = obj
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("read", help="Print current offsets as JSON")

    write_p = subparsers.add_parser("write", help="Write offsets")
    write_p.add_argument("--offsets", required=True, help="JSON string of offsets to write")

    rc_p = subparsers.add_parser("read-config", help="Read a config value by dot-path")
    rc_p.add_argument("key", help="Dot-path key, e.g. ragas.llm_judge")

    args = parser.parse_args()

    if args.command == "read":
        print(json.dumps(read_offsets(), indent=2))

    elif args.command == "write":
        offsets = json.loads(args.offsets)
        write_offsets(offsets)
        print("✅ Offsets written")

    elif args.command == "read-config":
        cfg = read_config()
        val = get_nested(cfg, args.key)
        print(val if val is not None else "")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
