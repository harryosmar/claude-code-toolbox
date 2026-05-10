"""Sync watched local folders into ChromaDB documents collection — NEW/MODIFIED/DELETED delta.

Reads `local_folders.watches[]` from config.json, compares each folder's current contents
against `local_folder.files` in offsets.json, and applies the diff:

  - NEW       (in folder, not in offsets)         → extract + embed
  - MODIFIED  (mtime or size changed)              → delete old chunks, re-embed (same doc_id)
  - DELETED   (in offsets, no longer in folder)    → delete chunks
  - UNCHANGED (mtime + size match)                 → skip

Idempotent. Safe to re-run repeatedly — only NEW/MODIFIED files do real work.

Doc-ID scheme: doc:{label}:{sha256(absolute_path)[:12]} — stable per-path-within-label,
so two files with the same name in different folders coexist, and re-embedding a modified
file overwrites cleanly via the same ID.
"""
import argparse
import fcntl  # POSIX only — Windows users would need msvcrt.locking or a cross-platform library
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterator, NamedTuple

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
CONFIG_PATH = os.path.join(WORK_INTEL_HOME, "config.json")
OFFSETS_PATH = os.path.join(WORK_INTEL_HOME, "offsets.json")
CHROMA_PATH = os.path.join(WORK_INTEL_HOME, "chroma")
LOCK_PATH = os.path.join(WORK_INTEL_HOME, "sync.lock")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt"]
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB — matches ingest-document skill cap
MAX_FILES_PER_WATCH = 10_000  # hard cap on files walked per watch to prevent runaway FS scans
COLLECTION = "documents"

def _unsafe_watch_path_reason(raw_path: str) -> str | None:
    """Return a human-readable refusal reason if `raw_path` is too broad, else None.

    Refuses paths that are:
      - exactly '/'  (filesystem root)
      - exactly $HOME
      - a direct child of '/' as written (e.g. /tmp, /home, /usr, /var)
      - whose realpath resolves to '/' or $HOME

    The raw-path check (before symlink resolution) catches platform aliases like /tmp on
    macOS which resolves to /private/tmp rather than a direct child of /.
    """
    expanded = os.path.expanduser(raw_path)
    resolved = os.path.realpath(expanded)
    home = os.path.realpath(os.path.expanduser("~"))

    if resolved == "/":
        return f"refused unsafe watch path '{raw_path}' (resolves to filesystem root '/')"
    if resolved == home:
        return f"refused unsafe watch path '{raw_path}' (resolves to $HOME '{home}')"

    # Catch direct children of '/' as written — covers /tmp, /home, /usr, /var, etc.
    # We use normpath on the expanded form (before realpath) so /tmp is caught even on macOS
    # where realpath('/tmp') == '/private/tmp'.
    normed = os.path.normpath(expanded)
    if os.path.dirname(normed) in ("/", ""):
        return (
            f"refused unsafe watch path '{raw_path}' "
            f"(resolves to top-level system directory '{normed}')"
        )
    return None


# Direct-import the sibling scripts so we load the embedding model once per sync run
# (heavy deps — sentence-transformers, chromadb, unstructured — are still lazy-imported
# inside the called functions, so importing these names is cheap).
sys.path.insert(0, SCRIPTS_DIR)
from extract_attachment import extract  # noqa: E402
from embed_item import embed_items  # noqa: E402


def load_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            print(f"ERROR: {path}: malformed JSON — {exc}", file=sys.stderr)
            sys.exit(2)


def write_offsets(offsets: dict[str, Any]) -> None:
    os.makedirs(WORK_INTEL_HOME, exist_ok=True)
    tmp = OFFSETS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(offsets, f, indent=2)
    os.replace(tmp, OFFSETS_PATH)


def doc_id_for(label: str, abs_path: str) -> str:
    h = hashlib.sha256(abs_path.encode()).hexdigest()[:12]
    return f"doc:{label}:{h}"


def walk_folder(
    path: str,
    extensions: list[str],
    recursive: bool,
    max_files: int = MAX_FILES_PER_WATCH,
) -> Iterator[str]:
    """Yield absolute paths matching extensions; skip hidden files/dirs.

    Stops after `max_files` matching files and prints a warning to stderr so the
    operator knows the cap fired (the walk is not silently truncated).
    """
    extset = {e.lower() for e in extensions}
    count = 0
    if recursive:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                if os.path.splitext(fname)[1].lower() not in extset:
                    continue
                if count >= max_files:
                    print(
                        f"WARNING: walk cap reached ({max_files} files) for '{path}' — "
                        "increase MAX_FILES_PER_WATCH or narrow the watch config.",
                        file=sys.stderr,
                    )
                    return
                yield os.path.join(root, fname)
                count += 1
    else:
        if not os.path.isdir(path):
            return
        for fname in os.listdir(path):
            if fname.startswith("."):
                continue
            full = os.path.join(path, fname)
            if not os.path.isfile(full):
                continue
            if os.path.splitext(fname)[1].lower() not in extset:
                continue
            if count >= max_files:
                print(
                    f"WARNING: walk cap reached ({max_files} files) for '{path}' — "
                    "increase MAX_FILES_PER_WATCH or narrow the watch config.",
                    file=sys.stderr,
                )
                return
            yield full
            count += 1


def stat_file(path: str) -> dict[str, int]:
    st = os.stat(path)
    # Use nanosecond mtime (integer, no float truncation) for sub-second change detection.
    # Existing offsets.json entries with the old "mtime" key lack "mtime_ns", so they will
    # be treated as MODIFIED on the first run after this upgrade — acceptable one-time re-index.
    return {"mtime_ns": st.st_mtime_ns, "size_bytes": st.st_size}


def delete_chunks(base_ids: list[str]) -> None:
    """Delete every chunk whose base_id metadata matches any of the given doc_ids."""
    if not base_ids:
        return
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    # ChromaDB's `where` accepts $in for batch matching; one call per batch is fine.
    if len(base_ids) == 1:
        collection.delete(where={"base_id": base_ids[0]})
    else:
        where: Any = {"base_id": {"$in": base_ids}}
        collection.delete(where=where)


def build_item(abs_path: str, label: str, doc_id: str) -> dict[str, Any] | None:
    """Extract text and assemble an item dict for embed_items. Returns None if extraction failed."""
    result = extract(abs_path)
    if result.get("error") or not (result.get("text") or "").strip():
        return None
    text = result["text"]
    content_hash = (result.get("metadata") or {}).get("content_hash", "")
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": doc_id,
        "source": "local_folder",
        "type": "document",
        "title": os.path.basename(abs_path),
        "body": text,
        "url": f"file://{abs_path}",
        "timestamp": now,
        "metadata": {
            "label": label,
            "path": abs_path,
            "ext": os.path.splitext(abs_path)[1].lower(),
            "content_hash": content_hash,
            "ingested_at": now,
        },
    }


class WatchClassification(NamedTuple):
    new_state_for_label: dict[str, Any]
    items_to_embed: list[dict[str, Any]]
    doc_ids_to_delete: list[str]
    errors: list[str]
    summary: dict[str, Any]


def classify_watch(watch: dict[str, Any], prev_state: dict[str, Any]) -> WatchClassification:
    """Walk one watched folder and classify its files.

    Returns a WatchClassification with fields:
      new_state_for_label, items_to_embed, doc_ids_to_delete, errors, summary.
    `new_state_for_label` is the post-sync offset entries for this label, with NEW/MODIFIED
    entries pre-filled with their new mtime/size — caller still has to verify the embed
    succeeded before persisting them.
    """
    path = os.path.expanduser(watch["path"])
    label = watch.get("label") or os.path.basename(path.rstrip("/")) or "default"
    extensions = watch.get("extensions", DEFAULT_EXTENSIONS)
    recursive = bool(watch.get("recursive", True))

    summary = {"label": label, "path": path,
               "new": 0, "modified": 0, "deleted": 0, "unchanged": 0}
    errors: list[str] = []
    items_to_embed: list[dict[str, Any]] = []
    doc_ids_to_delete: list[str] = []
    new_state_for_label: dict[str, Any] = {}

    refusal = _unsafe_watch_path_reason(watch["path"])
    if refusal:
        errors.append(refusal)
        return WatchClassification(
            new_state_for_label={p: e for p, e in prev_state.items() if e.get("label") == label},
            items_to_embed=items_to_embed,
            doc_ids_to_delete=doc_ids_to_delete,
            errors=errors,
            summary=summary,
        )

    if not os.path.isdir(path):
        errors.append(f"path does not exist: {path}")
        # Preserve prev entries for this label as-is (don't blow them away on a transient outage)
        return WatchClassification(
            new_state_for_label={p: e for p, e in prev_state.items() if e.get("label") == label},
            items_to_embed=items_to_embed,
            doc_ids_to_delete=doc_ids_to_delete,
            errors=errors,
            summary=summary,
        )

    # Walk current state
    current: dict[str, dict[str, int]] = {}
    for fp in walk_folder(path, extensions, recursive):
        try:
            st = stat_file(fp)
        except OSError as e:
            errors.append(f"stat failed {fp}: {e}")
            continue
        if st["size_bytes"] > MAX_FILE_SIZE_BYTES:
            errors.append(f"skipped (>50MB): {fp}")
            continue
        current[fp] = st

    prev_for_label = {p: e for p, e in prev_state.items() if e.get("label") == label}

    # NEW / MODIFIED / UNCHANGED
    for fp, st in current.items():
        prev = prev_for_label.get(fp)
        if not prev:
            doc_id = doc_id_for(label, fp)
            item = build_item(fp, label, doc_id)
            if not item:
                errors.append(f"extract failed: {fp}")
                continue
            items_to_embed.append(item)
            new_state_for_label[fp] = {
                "label": label, "mtime_ns": st["mtime_ns"], "size_bytes": st["size_bytes"],
                "doc_id": doc_id, "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
            summary["new"] += 1
        elif prev.get("mtime_ns") != st["mtime_ns"] or prev["size_bytes"] != st["size_bytes"]:
            doc_id = prev["doc_id"]
            item = build_item(fp, label, doc_id)
            if not item:
                errors.append(f"extract failed (modified): {fp}")
                # Preserve prev entry — old chunks stay; we'll retry next run
                new_state_for_label[fp] = prev
                continue
            doc_ids_to_delete.append(doc_id)
            items_to_embed.append(item)
            new_state_for_label[fp] = {
                **prev, "mtime_ns": st["mtime_ns"], "size_bytes": st["size_bytes"],
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
            summary["modified"] += 1
        else:
            new_state_for_label[fp] = prev
            summary["unchanged"] += 1

    # DELETED — in prev but missing on disk
    for fp, prev in prev_for_label.items():
        if fp not in current:
            doc_ids_to_delete.append(prev["doc_id"])
            summary["deleted"] += 1
            # Intentionally NOT added to new_state_for_label — entry is dropped from offsets

    return WatchClassification(
        new_state_for_label=new_state_for_label,
        items_to_embed=items_to_embed,
        doc_ids_to_delete=doc_ids_to_delete,
        errors=errors,
        summary=summary,
    )


def sync_all(only_label: str | None = None) -> dict[str, Any]:
    """Sync every configured watched folder (or just one if `only_label` given)."""
    os.makedirs(WORK_INTEL_HOME, exist_ok=True)
    with open(LOCK_PATH, "w") as lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"error": "another sync is in progress (sync.lock held)", "summaries": []}
        try:
            return _sync_all_locked(only_label)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _sync_all_locked(only_label: str | None = None) -> dict[str, Any]:
    """Inner sync body — must be called only while sync.lock is held."""
    config = load_json(CONFIG_PATH)
    offsets = load_json(OFFSETS_PATH)

    watches = (config.get("local_folders") or {}).get("watches") or []
    if only_label:
        watches = [w for w in watches if w.get("label") == only_label]
        if not watches:
            return {"error": f"no watch with label '{only_label}'", "summaries": []}
    if not watches:
        return {"error": "no watches configured (config.local_folders.watches is empty)",
                "summaries": []}

    prev_state = (offsets.get("local_folder") or {}).get("files") or {}
    touched_labels = {w.get("label") for w in watches}
    # Preserve entries for labels we are NOT touching this run (untouched watches stay intact)
    new_state: dict[str, Any] = {p: e for p, e in prev_state.items()
                                  if e.get("label") not in touched_labels}

    summaries: list[dict[str, Any]] = []
    all_items_to_embed: list[dict[str, Any]] = []
    all_doc_ids_to_delete: list[str] = []
    item_path_for_id: dict[str, str] = {}  # doc_id -> abs_path, so we can drop failed items from state
    # Map from doc_id to the summary dict that owns its count, so we can decrement on failure
    item_summary_for_id: dict[str, dict[str, Any]] = {}

    for watch in watches:
        result = classify_watch(watch, prev_state)
        new_state.update(result.new_state_for_label)
        all_items_to_embed.extend(result.items_to_embed)
        all_doc_ids_to_delete.extend(result.doc_ids_to_delete)
        for item in result.items_to_embed:
            item_path_for_id[item["id"]] = item["metadata"]["path"]
            item_summary_for_id[item["id"]] = result.summary
        result.summary["errors"] = result.errors
        summaries.append(result.summary)

    # Delete first — for MODIFIED files we need old chunks gone before the new upsert lands
    if all_doc_ids_to_delete:
        try:
            delete_chunks(all_doc_ids_to_delete)
        except Exception as e:
            for s in summaries:
                s["errors"].append(f"chromadb delete failed: {e}")

    # Then embed everything in one shot — model loads exactly once for the whole run
    if all_items_to_embed:
        try:
            embed_result = embed_items(all_items_to_embed, COLLECTION)
            failed_ids = set(embed_result.get("FAILED_ITEMS") or [])
            # Drop failed items from new_state so they get retried next sync
            for failed_id in failed_ids:
                failed_path = item_path_for_id.get(failed_id)
                if failed_path and failed_path in new_state:
                    # Restore previous entry if there was one (modified case), else drop entirely
                    prev = prev_state.get(failed_path)
                    if prev:
                        new_state[failed_path] = prev
                        item_summary_for_id[failed_id]["modified"] = max(
                            0, item_summary_for_id[failed_id]["modified"] - 1)
                    else:
                        del new_state[failed_path]
                        item_summary_for_id[failed_id]["new"] = max(
                            0, item_summary_for_id[failed_id]["new"] - 1)
                    item_summary_for_id[failed_id]["errors"].append(f"embed failed: {failed_path}")
        except Exception as e:
            for s in summaries:
                s["errors"].append(f"embed_items raised: {e}")
                s["embed_failed"] = s["new"] + s["modified"]
                s["new"] = 0
                s["modified"] = 0
            # On total embed failure, drop the NEW entries we tentatively added so we retry next time
            for item in all_items_to_embed:
                fp = item["metadata"]["path"]
                prev = prev_state.get(fp)
                if prev:
                    new_state[fp] = prev
                elif fp in new_state:
                    del new_state[fp]

    offsets.setdefault("local_folder", {})
    offsets["local_folder"]["files"] = new_state
    offsets["local_folder"]["last_scan_ts"] = datetime.now(timezone.utc).isoformat()
    write_offsets(offsets)

    return {"error": None, "summaries": summaries}


def print_summaries(result: dict[str, Any]) -> int:
    if result.get("error"):
        print(f"ERROR {result['error']}", file=sys.stderr)
        print("SYNC_FOLDER_RESULT: FAILED")
        print("FILES_NEW: 0")
        print("FILES_MODIFIED: 0")
        print("FILES_DELETED: 0")
        return 1

    total_new = total_mod = total_del = total_err = total_embed_failed = 0
    any_partial = False
    for s in result["summaries"]:
        total_new += s["new"]
        total_mod += s["modified"]
        total_del += s["deleted"]
        total_embed_failed += s.get("embed_failed", 0)
        errs = s.get("errors") or []
        total_err += len(errs)
        if errs:
            any_partial = True
        embed_failed_part = (f", {s['embed_failed']} embed-failed"
                             if s.get("embed_failed") else "")
        suffix = f" ({len(errs)} errors)" if errs else ""
        print(f"folder[{s['label']}]: {s['new']} new, {s['modified']} modified, "
              f"{s['deleted']} deleted, {s['unchanged']} unchanged"
              f"{embed_failed_part}{suffix}")
        for err in errs:
            print(f"   warn: {err}", file=sys.stderr)

    if total_err and (total_new == 0 and total_mod == 0 and total_del == 0):
        overall = "FAILED"
    elif any_partial:
        overall = "PARTIAL"
    else:
        overall = "OK"
    print(f"SYNC_FOLDER_RESULT: {overall}")
    print(f"FILES_NEW: {total_new}")
    print(f"FILES_MODIFIED: {total_mod}")
    print(f"FILES_DELETED: {total_del}")
    if total_embed_failed:
        print(f"FILES_EMBED_FAILED: {total_embed_failed}")
    return 0 if overall != "FAILED" else 1


def main():
    parser = argparse.ArgumentParser(
        description="Sync watched folders into ChromaDB (delta: NEW/MODIFIED/DELETED).")
    parser.add_argument("--label", help="Sync only the watch with this label")
    args = parser.parse_args()
    result = sync_all(only_label=args.label)
    sys.exit(print_summaries(result))


if __name__ == "__main__":
    main()
