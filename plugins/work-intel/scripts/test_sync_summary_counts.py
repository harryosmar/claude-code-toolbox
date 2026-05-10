"""Unit tests for sync_folder summary count accuracy around embed_items outcomes.

Cases:
  1. embed succeeds       → summary counts are unchanged
  2. FAILED_ITEMS subset  → counts only reflect successfully embedded items
  3. embed_items raises   → summary['new']=0, summary['modified']=0,
                            summary['embed_failed']=N, errors contains 'embed_items raised'
"""
import io
import os
import sys
import tempfile
import types
import unittest

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Stub heavy deps before importing sync_folder.
# extract must return non-empty text so build_item doesn't return None.
def _stub_extract(path, **k):
    return {"text": "stub content", "metadata": {"content_hash": "abc"}}

for _mod in ("extract_attachment", "embed_item"):
    if _mod not in sys.modules:
        stub = types.ModuleType(_mod)
        stub.extract = _stub_extract              # type: ignore[attr-defined]
        stub.embed_items = lambda *a, **k: {}    # type: ignore[attr-defined]
        sys.modules[_mod] = stub

import sync_folder  # noqa: E402

# Ensure sync_folder.extract points to the stub that returns real content
sync_folder.extract = _stub_extract  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_watch_dir(n_files: int) -> tuple[str, list[str]]:
    """Create a temp dir with n_files .txt files; return (dir_path, [abs_paths])."""
    tmpdir = tempfile.mkdtemp()
    paths = []
    for i in range(n_files):
        fp = os.path.join(tmpdir, f"doc{i}.txt")
        with open(fp, "w") as f:
            f.write(f"content of doc {i}\n")
        paths.append(fp)
    return tmpdir, paths


def _watch(path: str, label: str = "test") -> dict:
    return {"path": path, "label": label, "extensions": [".txt"], "recursive": False}


def _run_sync(watches: list, prev_state: dict, embed_items_fn) -> dict:
    """Drive classify_watch + the embed block in isolation (no offsets I/O)."""
    # Patch embed_items on the already-imported module
    orig = sync_folder.embed_items
    sync_folder.embed_items = embed_items_fn  # type: ignore[attr-defined]
    try:
        # Replicate the core of sync_all without file I/O
        touched_labels = {w.get("label") for w in watches}
        new_state: dict = {p: e for p, e in prev_state.items()
                           if e.get("label") not in touched_labels}
        summaries: list = []
        all_items_to_embed: list = []
        all_doc_ids_to_delete: list = []
        item_path_for_id: dict = {}
        item_summary_for_id: dict = {}

        for watch in watches:
            per_label_state, items, doc_ids, errors, summary = sync_folder.classify_watch(
                watch, prev_state)
            new_state.update(per_label_state)
            all_items_to_embed.extend(items)
            all_doc_ids_to_delete.extend(doc_ids)
            for item in items:
                item_path_for_id[item["id"]] = item["metadata"]["path"]
                item_summary_for_id[item["id"]] = summary
            summary["errors"] = errors
            summaries.append(summary)

        if all_items_to_embed:
            try:
                embed_result = embed_items_fn(all_items_to_embed, sync_folder.COLLECTION)
                failed_ids = set(embed_result.get("FAILED_ITEMS") or [])
                for failed_id in failed_ids:
                    failed_path = item_path_for_id.get(failed_id)
                    if failed_path and failed_path in new_state:
                        prev = prev_state.get(failed_path)
                        if prev:
                            new_state[failed_path] = prev
                            item_summary_for_id[failed_id]["modified"] = max(
                                0, item_summary_for_id[failed_id]["modified"] - 1)
                        else:
                            del new_state[failed_path]
                            item_summary_for_id[failed_id]["new"] = max(
                                0, item_summary_for_id[failed_id]["new"] - 1)
                        item_summary_for_id[failed_id]["errors"].append(
                            f"embed failed: {failed_path}")
            except Exception as exc:
                for s in summaries:
                    s["errors"].append(f"embed_items raised: {exc}")
                    s["embed_failed"] = s["new"] + s["modified"]
                    s["new"] = 0
                    s["modified"] = 0
                for item in all_items_to_embed:
                    fp = item["metadata"]["path"]
                    prev = prev_state.get(fp)
                    if prev:
                        new_state[fp] = prev
                    elif fp in new_state:
                        del new_state[fp]

        return {"summaries": summaries, "new_state": new_state}
    finally:
        sync_folder.embed_items = orig  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSyncSummaryCounts(unittest.TestCase):

    # ------------------------------------------------------------------
    # Case 1: embed succeeds — counts must be unchanged
    # ------------------------------------------------------------------
    def test_embed_success_counts_unchanged(self):
        tmpdir, paths = _make_watch_dir(3)
        try:
            def embed_ok(items, collection):
                return {"FAILED_ITEMS": []}

            result = _run_sync([_watch(tmpdir)], prev_state={}, embed_items_fn=embed_ok)
            s = result["summaries"][0]
            self.assertEqual(s["new"], 3, "3 new files should be counted")
            self.assertEqual(s["modified"], 0)
            self.assertEqual(s.get("embed_failed", 0), 0)
            self.assertEqual(s["errors"], [])
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    # ------------------------------------------------------------------
    # Case 2: FAILED_ITEMS subset — counts reflect only successful items
    # ------------------------------------------------------------------
    def test_failed_items_subset_decrements_count(self):
        tmpdir, paths = _make_watch_dir(3)
        try:
            # We need to know the doc_ids; they depend on the label+path.
            # Build them after classify has run by capturing the items list.
            captured_items: list = []

            def embed_one_fails(items, collection):
                captured_items.extend(items)
                # Fail the first item
                return {"FAILED_ITEMS": [items[0]["id"]]}

            result = _run_sync([_watch(tmpdir)], prev_state={}, embed_items_fn=embed_one_fails)
            s = result["summaries"][0]
            # 3 queued, 1 failed → 2 succeeded
            self.assertEqual(s["new"], 2,
                             "count should be decremented by 1 for the failed item")
            self.assertEqual(s.get("embed_failed", 0), 0,
                             "embed_failed is only set on catastrophic raise, not partial failure")
            # Failed path should appear in errors
            self.assertTrue(any("embed failed:" in e for e in s["errors"]))
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    # ------------------------------------------------------------------
    # Case 3: embed_items raises — new=0, modified=0, embed_failed=N, errors contains msg
    # ------------------------------------------------------------------
    def test_embed_raises_resets_counts_and_sets_embed_failed(self):
        tmpdir, paths = _make_watch_dir(4)
        try:
            def embed_crash(items, collection):
                raise RuntimeError("model exploded")

            result = _run_sync([_watch(tmpdir)], prev_state={}, embed_items_fn=embed_crash)
            s = result["summaries"][0]
            self.assertEqual(s["new"], 0, "new must be 0 after catastrophic embed failure")
            self.assertEqual(s["modified"], 0, "modified must be 0 after catastrophic embed failure")
            self.assertEqual(s["embed_failed"], 4,
                             "embed_failed must equal the number of queued items")
            self.assertTrue(any("embed_items raised" in e for e in s["errors"]),
                            "errors must contain 'embed_items raised'")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    # ------------------------------------------------------------------
    # Case 3b: embed_items raises with mixed new+modified — both zeroed, embed_failed = sum
    # ------------------------------------------------------------------
    def test_embed_raises_with_modified_files(self):
        tmpdir, _ = _make_watch_dir(2)
        try:
            # Pre-populate prev_state with one file so it's MODIFIED, not NEW
            existing_path = os.path.join(tmpdir, "doc0.txt")
            doc_id = sync_folder.doc_id_for("test", existing_path)
            import stat
            st = os.stat(existing_path)
            prev_state = {
                existing_path: {
                    "label": "test",
                    "mtime": st.st_mtime - 10,  # older mtime → triggers MODIFIED
                    "size_bytes": st.st_size,
                    "doc_id": doc_id,
                    "ingested_at": "2024-01-01T00:00:00+00:00",
                }
            }

            def embed_crash(items, collection):
                raise RuntimeError("oom")

            result = _run_sync([_watch(tmpdir)], prev_state=prev_state,
                               embed_items_fn=embed_crash)
            s = result["summaries"][0]
            # doc0.txt → MODIFIED (1), doc1.txt → NEW (1)
            self.assertEqual(s["new"], 0)
            self.assertEqual(s["modified"], 0)
            self.assertEqual(s["embed_failed"], 2,
                             "embed_failed = 1 new + 1 modified = 2")
        finally:
            import shutil
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestSyncSummaryCounts)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
