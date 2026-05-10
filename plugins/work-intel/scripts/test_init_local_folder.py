"""Unit tests for init_local_folder in init_source.py.

Cases:
  1. No watched folders configured → returns 0, writes placeholder offset.
  2. sync_all returns an error → prints warning, returns 0.
  3. Normal success path → returns sum of s['new'] across summaries.
  4. Success with errors in a summary → output includes error suffix.
"""
import io
import os
import sys
import types
import unittest
from unittest.mock import patch

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import init_source


def _sync_folder_stub(fake_result: dict) -> types.ModuleType:
    """Return a minimal sync_folder module substitute whose sync_all returns fake_result."""
    stub = types.ModuleType("sync_folder")
    stub.sync_all = lambda: fake_result  # type: ignore[attr-defined]
    return stub


class TestInitLocalFolder(unittest.TestCase):

    # ------------------------------------------------------------------
    # Case 1: no watches configured → placeholder written, returns 0
    # ------------------------------------------------------------------
    def test_no_watches_returns_zero_and_writes_placeholder(self):
        with patch.object(init_source, "write_offset") as mock_write:
            result = init_source.init_local_folder({})

        self.assertEqual(result, 0)
        mock_write.assert_called_once_with(
            "local_folder", {"files": {}, "last_scan_ts": "INIT_PENDING"}
        )

    def test_empty_watches_list_returns_zero(self):
        cfg = {"local_folders": {"watches": []}}
        with patch.object(init_source, "write_offset") as mock_write:
            result = init_source.init_local_folder(cfg)
        self.assertEqual(result, 0)
        mock_write.assert_called_once()

    # ------------------------------------------------------------------
    # Case 2: sync_all returns error → prints warning, returns 0
    # ------------------------------------------------------------------
    def test_sync_all_error_returns_zero(self):
        cfg = {"local_folders": {"watches": [{"path": "/tmp/x", "label": "t"}]}}
        fake = {"error": "path does not exist: /tmp/x", "summaries": []}

        with patch.dict("sys.modules", {"sync_folder": _sync_folder_stub(fake)}):
            result = init_source.init_local_folder(cfg)

        self.assertEqual(result, 0)

    # ------------------------------------------------------------------
    # Case 3: normal success → returns sum of s['new']
    # ------------------------------------------------------------------
    def test_success_returns_sum_of_new_files(self):
        cfg = {"local_folders": {"watches": [
            {"path": "/tmp/a", "label": "alpha"},
            {"path": "/tmp/b", "label": "beta"},
        ]}}
        fake = {
            "error": None,
            "summaries": [
                {"label": "alpha", "new": 3, "errors": []},
                {"label": "beta",  "new": 5, "errors": []},
            ],
        }
        with patch.dict("sys.modules", {"sync_folder": _sync_folder_stub(fake)}):
            result = init_source.init_local_folder(cfg)

        self.assertEqual(result, 8)

    # ------------------------------------------------------------------
    # Case 4: success with per-summary errors → output includes suffix
    # ------------------------------------------------------------------
    def test_success_with_errors_includes_error_suffix(self):
        cfg = {"local_folders": {"watches": [{"path": "/tmp/a", "label": "docs"}]}}
        fake = {
            "error": None,
            "summaries": [
                {"label": "docs", "new": 2, "errors": ["extract failed: bad.pdf"]},
            ],
        }
        captured = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = captured
        try:
            with patch.dict("sys.modules", {"sync_folder": _sync_folder_stub(fake)}):
                result = init_source.init_local_folder(cfg)
        finally:
            sys.stdout = orig_stdout

        self.assertEqual(result, 2)
        self.assertIn("(1 errors)", captured.getvalue())


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestInitLocalFolder)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
