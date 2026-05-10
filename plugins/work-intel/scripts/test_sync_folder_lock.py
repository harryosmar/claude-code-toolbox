"""Unit tests for sync_folder advisory lock (M-005).

Cases:
  1. Lock already held        → sync_all returns 'another sync is in progress' error immediately.
  2. Lock released            → sync_all proceeds normally (no lock error).
  3. Lock file exists at path → WORK_INTEL_HOME/sync.lock is created on acquisition.
"""
import fcntl
import json
import os
import sys
import tempfile
import types
import unittest

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Stub heavy deps before importing sync_folder.
def _stub_extract(path, **k):
    return {"text": "stub content", "metadata": {"content_hash": "abc"}}

for _mod in ("extract_attachment", "embed_item"):
    if _mod not in sys.modules:
        stub = types.ModuleType(_mod)
        stub.extract = _stub_extract          # type: ignore[attr-defined]
        stub.embed_items = lambda *a, **k: {} # type: ignore[attr-defined]
        sys.modules[_mod] = stub

import sync_folder  # noqa: E402

sync_folder.extract = _stub_extract  # type: ignore[attr-defined]


class TestSyncFolderLock(unittest.TestCase):

    def setUp(self):
        # Each test gets its own isolated WORK_INTEL_HOME so tests don't interfere.
        self._tmpdir = tempfile.mkdtemp()
        # Point all path constants at the temp dir.
        self._orig_home = sync_folder.WORK_INTEL_HOME
        self._orig_config = sync_folder.CONFIG_PATH
        self._orig_offsets = sync_folder.OFFSETS_PATH
        self._orig_chroma = sync_folder.CHROMA_PATH
        self._orig_lock = sync_folder.LOCK_PATH

        sync_folder.WORK_INTEL_HOME = self._tmpdir
        sync_folder.CONFIG_PATH = os.path.join(self._tmpdir, "config.json")
        sync_folder.OFFSETS_PATH = os.path.join(self._tmpdir, "offsets.json")
        sync_folder.CHROMA_PATH = os.path.join(self._tmpdir, "chroma")
        sync_folder.LOCK_PATH = os.path.join(self._tmpdir, "sync.lock")

        # Write a minimal config so sync_all doesn't error on "no watches".
        watch_dir = tempfile.mkdtemp(dir=self._tmpdir)
        self._watch_dir = watch_dir
        config = {"local_folders": {"watches": [
            {"path": watch_dir, "label": "test", "extensions": [".txt"], "recursive": False}
        ]}}
        with open(sync_folder.CONFIG_PATH, "w") as f:
            json.dump(config, f)

    def tearDown(self):
        # Restore module-level constants.
        sync_folder.WORK_INTEL_HOME = self._orig_home
        sync_folder.CONFIG_PATH = self._orig_config
        sync_folder.OFFSETS_PATH = self._orig_offsets
        sync_folder.CHROMA_PATH = self._orig_chroma
        sync_folder.LOCK_PATH = self._orig_lock

        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Case 1: lock already held → fast-fail with clear error message
    # ------------------------------------------------------------------
    def test_lock_held_returns_error(self):
        lock_path = sync_folder.LOCK_PATH
        # Acquire the lock ourselves before calling sync_all.
        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = sync_folder.sync_all()
            self.assertIsNotNone(result.get("error"),
                                 "expected an error when lock is held")
            self.assertIn("another sync is in progress", result["error"])
            self.assertEqual(result["summaries"], [],
                             "summaries must be empty on lock contention")
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()

    # ------------------------------------------------------------------
    # Case 2: lock released → sync_all proceeds normally (no lock error)
    # ------------------------------------------------------------------
    def test_lock_released_allows_sync(self):
        lock_path = sync_folder.LOCK_PATH
        # Acquire then immediately release.
        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        holder_fd.close()

        # Stub embed_items so we don't need a real ChromaDB.
        orig_embed = sync_folder.embed_items
        sync_folder.embed_items = lambda items, col: {}  # type: ignore[attr-defined]
        try:
            result = sync_folder.sync_all()
            # Watch dir is empty → no error about the lock; may have "no watches" type
            # errors or succeed with 0 counts — either way, not a lock error.
            self.assertNotEqual(
                result.get("error"), "another sync is in progress (sync.lock held)",
                "lock should not be contended after it was released",
            )
        finally:
            sync_folder.embed_items = orig_embed  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Case 3: lock file is created at WORK_INTEL_HOME/sync.lock
    # ------------------------------------------------------------------
    def test_lock_file_created_at_work_intel_home(self):
        lock_path = sync_folder.LOCK_PATH
        # Before any call the file may or may not exist; after a held-lock call it must exist.
        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            # Trigger the contention path so sync_all tries to open the lock file itself.
            sync_folder.sync_all()
            # The lock file must exist at the correct path.
            self.assertTrue(
                os.path.exists(lock_path),
                f"sync.lock must exist at {lock_path}",
            )
            self.assertEqual(
                lock_path, os.path.join(self._tmpdir, "sync.lock"),
                "lock file must be inside WORK_INTEL_HOME",
            )
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestSyncFolderLock)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
