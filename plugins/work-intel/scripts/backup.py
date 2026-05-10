"""Create a portable backup tarball of work-intel data (excludes OAuth tokens)."""
import argparse
import hashlib
import os
import re
import sys
import tarfile

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))

EXCLUDED_PATTERNS = [
    re.compile(r".*\.token$", re.IGNORECASE),
    re.compile(r".*\.oauth$", re.IGNORECASE),
    re.compile(r".*credentials\.json$", re.IGNORECASE),
    re.compile(r".*secret.*", re.IGNORECASE),
    re.compile(r".*\.key$", re.IGNORECASE),
    re.compile(r".*api[_-]?key.*", re.IGNORECASE),
]

# Subdirectories anywhere in the tree that should never be packed.
# Used to exclude raw pre-redaction text under security-audit/_raw/.
EXCLUDED_DIR_NAMES = {"_raw"}

INCLUDED_PATHS = [
    "chroma",
    "config.json",
    "offsets.json",
    "ragas-testset.json",
    "ragas-reports",
    "security-audit",  # security audit reports (excludes _raw/ subdir at walk time)
]


def is_excluded(path: str) -> bool:
    name = os.path.basename(path)
    return any(p.match(name) for p in EXCLUDED_PATTERNS)


def create_backup(source: str, output: str) -> int:
    total = 0
    with tarfile.open(output, "w:gz") as tar:
        for include in INCLUDED_PATHS:
            full = os.path.join(source, include)
            if not os.path.exists(full):
                continue
            if os.path.isfile(full):
                if not is_excluded(full):
                    tar.add(full, arcname=include)
                    total += 1
            elif os.path.isdir(full):
                for root, dirs, files in os.walk(full):
                    # Prune excluded subdirectories in-place so os.walk skips them.
                    dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_NAMES]
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        if not is_excluded(fpath):
                            arcname = os.path.relpath(fpath, source)
                            tar.add(fpath, arcname=arcname)
                            total += 1
    return total


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=WORK_INTEL_HOME)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if not os.path.isdir(args.source):
        print(f"❌ Source directory not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    count = create_backup(args.source, args.output)
    size = os.path.getsize(args.output)
    checksum = sha256_file(args.output)

    size_str = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.1f} KB"
    print(f"✅ Backup created: {args.output}")
    print(f"   Files packed: {count}")
    print(f"   Size: {size_str}")
    print(f"   SHA256: {checksum}")


if __name__ == "__main__":
    main()
