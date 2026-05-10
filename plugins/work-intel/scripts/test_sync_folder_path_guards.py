"""Unit tests for sync_folder path-safety guards (L-001).

Cases:
  1. watch.path resolving to '/'      → refused with 'refused unsafe watch path' in errors.
  2. watch.path resolving to $HOME    → refused with 'refused unsafe watch path' in errors.
  3. watch.path with 11000 matching files → walk yields exactly MAX_FILES_PER_WATCH files
     and emits a 'walk cap reached' warning to stderr.
  4. Normal watch with 5 matching files → all 5 are yielded cleanly, no cap warning.
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


class TestUnsafePathRefusal(unittest.TestCase):
    """Cases 1 & 2: refused paths end up in summary['errors'], not a sys.exit."""

    def _make_watch(self, raw_path: str) -> dict:
        return {"path": raw_path, "label": "danger", "extensions": [".txt"], "recursive": True}

    def _run_classify(self, raw_path: str):
        watch = self._make_watch(raw_path)
        _, _, _, errors, summary = sync_folder.classify_watch(watch, {})
        return errors, summary

    # ------------------------------------------------------------------
    # Case 1: path resolves to '/'
    # ------------------------------------------------------------------
    def test_root_path_is_refused(self):
        errors, summary = self._run_classify("/")
        self.assertTrue(
            any("refused unsafe watch path" in e for e in errors),
            f"expected 'refused unsafe watch path' error for '/', got: {errors}",
        )
        self.assertEqual(summary["new"], 0)
        self.assertEqual(summary["modified"], 0)

    # ------------------------------------------------------------------
    # Case 2: path resolves to $HOME
    # ------------------------------------------------------------------
    def test_home_path_is_refused(self):
        home = os.path.expanduser("~")
        errors, summary = self._run_classify(home)
        self.assertTrue(
            any("refused unsafe watch path" in e for e in errors),
            f"expected 'refused unsafe watch path' error for $HOME, got: {errors}",
        )
        self.assertEqual(summary["new"], 0)
        self.assertEqual(summary["modified"], 0)

    def test_home_tilde_is_refused(self):
        """~ shorthand must also be caught after expanduser."""
        errors, _ = self._run_classify("~")
        self.assertTrue(
            any("refused unsafe watch path" in e for e in errors),
            f"expected 'refused unsafe watch path' error for '~', got: {errors}",
        )

    def test_top_level_system_dir_is_refused(self):
        """Paths like /tmp (depth 1 from root) must be refused."""
        errors, _ = self._run_classify("/tmp")
        self.assertTrue(
            any("refused unsafe watch path" in e for e in errors),
            f"expected 'refused unsafe watch path' error for '/tmp', got: {errors}",
        )

    def test_safe_nested_path_is_not_refused(self):
        """A two-segment path like /tmp/myproject must NOT be refused."""
        with tempfile.TemporaryDirectory() as d:
            # d is something like /tmp/tmpXXXXXX — depth 2 from root → safe
            reason = sync_folder._unsafe_watch_path_reason(d)
            self.assertIsNone(reason, f"safe path '{d}' was incorrectly refused: {reason}")


class TestWalkFolderCap(unittest.TestCase):
    """Cases 3 & 4: file-count cap and normal walk."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Case 3: 11 000 matching files → exactly MAX_FILES_PER_WATCH yielded + stderr warning
    # ------------------------------------------------------------------
    def test_walk_cap_fires_at_max_files(self):
        cap = sync_folder.MAX_FILES_PER_WATCH  # 10 000
        total_files = cap + 1000               # 11 000

        # Create files in subdirs to keep the OS happy (avoid huge single-dir listings).
        batch = 200
        for i in range(0, total_files, batch):
            sub = os.path.join(self._tmpdir, f"sub{i}")
            os.makedirs(sub, exist_ok=True)
            for j in range(min(batch, total_files - i)):
                open(os.path.join(sub, f"f{i+j}.txt"), "w").close()

        captured_stderr = io.StringIO()
        orig_stderr = sys.stderr
        sys.stderr = captured_stderr
        try:
            found = list(sync_folder.walk_folder(self._tmpdir, [".txt"], recursive=True))
        finally:
            sys.stderr = orig_stderr

        self.assertEqual(
            len(found), cap,
            f"expected exactly {cap} files from capped walk, got {len(found)}",
        )
        warning_output = captured_stderr.getvalue()
        self.assertIn(
            "walk cap reached",
            warning_output,
            f"expected 'walk cap reached' in stderr, got: {warning_output!r}",
        )

    # ------------------------------------------------------------------
    # Case 4: 5 matching files → all 5 yielded, no cap warning
    # ------------------------------------------------------------------
    def test_normal_walk_yields_all_files(self):
        for i in range(5):
            open(os.path.join(self._tmpdir, f"file{i}.txt"), "w").close()
        # Add a non-matching file to confirm extension filter still works.
        open(os.path.join(self._tmpdir, "ignored.pdf"), "w").close()

        captured_stderr = io.StringIO()
        orig_stderr = sys.stderr
        sys.stderr = captured_stderr
        try:
            found = list(sync_folder.walk_folder(self._tmpdir, [".txt"], recursive=False))
        finally:
            sys.stderr = orig_stderr

        self.assertEqual(len(found), 5, f"expected 5 files, got {len(found)}")
        self.assertEqual(
            captured_stderr.getvalue(), "",
            "no cap warning expected for 5 files",
        )


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestUnsafePathRefusal))
    suite.addTests(loader.loadTestsFromTestCase(TestWalkFolderCap))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
