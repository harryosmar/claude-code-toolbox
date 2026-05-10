"""Unit tests for load_json in sync_folder.py."""
import io
import json
import os
import sys
import tempfile
import unittest

# Ensure the scripts directory is on the path so we can import sync_folder
# without triggering the heavy sibling imports (extract_attachment, embed_item).
# We stub those out before the import.
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Stub heavy deps that sync_folder imports at module level
import types
for _mod in ("extract_attachment", "embed_item"):
    stub = types.ModuleType(_mod)
    stub.extract = lambda *a, **k: {}       # type: ignore[attr-defined]
    stub.embed_items = lambda *a, **k: {}   # type: ignore[attr-defined]
    sys.modules.setdefault(_mod, stub)

import sync_folder  # noqa: E402  (must come after stubs)


class TestLoadJson(unittest.TestCase):

    def test_valid_json_returns_parsed_dict(self):
        data = {"key": "value", "num": 42}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            result = sync_folder.load_json(path)
            self.assertEqual(result, data)
        finally:
            os.unlink(path)

    def test_invalid_json_exits_2_with_stderr_naming_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{this is not valid json")
            path = f.name
        try:
            captured_stderr = io.StringIO()
            _orig_stderr = sys.stderr
            sys.stderr = captured_stderr
            try:
                with self.assertRaises(SystemExit) as ctx:
                    sync_folder.load_json(path)
            finally:
                sys.stderr = _orig_stderr

            self.assertEqual(ctx.exception.code, 2)
            err_output = captured_stderr.getvalue()
            self.assertIn(path, err_output,
                          "stderr message must name the offending file path")
        finally:
            os.unlink(path)

    def test_nonexistent_path_returns_empty_dict(self):
        result = sync_folder.load_json("/nonexistent/path/that/does/not/exist.json")
        self.assertEqual(result, {})


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestLoadJson)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
