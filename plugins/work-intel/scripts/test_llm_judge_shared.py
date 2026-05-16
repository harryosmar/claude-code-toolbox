"""Unit tests for the shared judge factory in _llm_judge.py.

Verifies provider routing (anthropic/ollama), remote-Ollama base_url plumbing,
and backward-compat fallback to legacy `ragas.*` config fields.

No network, no LLM — langchain_anthropic and langchain_ollama are stubbed.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)


def _install_langchain_stubs():
    """Stub the two langchain providers so importing _llm_judge doesn't pull them."""
    if "langchain_anthropic" not in sys.modules:
        ma = types.ModuleType("langchain_anthropic")
        ma.ChatAnthropic = MagicMock(name="ChatAnthropic")  # type: ignore[attr-defined]
        sys.modules["langchain_anthropic"] = ma
    if "langchain_ollama" not in sys.modules:
        mo = types.ModuleType("langchain_ollama")
        mo.ChatOllama = MagicMock(name="ChatOllama")  # type: ignore[attr-defined]
        sys.modules["langchain_ollama"] = mo


_install_langchain_stubs()
import _llm_judge  # noqa: E402


class TestJudgeBlock(unittest.TestCase):
    """The legacy ragas.* fallback is the trickiest piece — exercise it directly."""

    def test_top_level_judge_block_wins(self):
        cfg = {
            "judge": {"provider": "anthropic", "anthropic_model": "haiku-x"},
            "ragas": {"llm_judge": "ollama", "anthropic_model": "haiku-y"},
        }
        block = _llm_judge._judge_block(cfg)
        self.assertEqual(block["provider"], "anthropic")
        self.assertEqual(block["anthropic_model"], "haiku-x")

    def test_legacy_ragas_block_used_when_no_judge(self):
        cfg = {"ragas": {"llm_judge": "anthropic", "anthropic_model": "haiku-z"}}
        block = _llm_judge._judge_block(cfg)
        self.assertEqual(block["provider"], "anthropic")
        self.assertEqual(block["anthropic_model"], "haiku-z")

    def test_partial_judge_block_falls_back_per_field(self):
        # Top-level judge overrides only what it sets; missing fields fall to ragas.*
        cfg = {
            "judge": {"provider": "ollama", "ollama_base_url": "http://192.0.2.10:11434"},
            "ragas": {"ollama_model": "mistral"},
        }
        block = _llm_judge._judge_block(cfg)
        self.assertEqual(block["provider"], "ollama")
        self.assertEqual(block["ollama_model"], "mistral")
        self.assertEqual(block["ollama_base_url"], "http://192.0.2.10:11434")


class TestGetJudge(unittest.TestCase):

    def setUp(self):
        # Reset call counts so each test starts clean.
        sys.modules["langchain_anthropic"].ChatAnthropic.reset_mock()
        sys.modules["langchain_ollama"].ChatOllama.reset_mock()

    def test_anthropic_provider_uses_chat_anthropic(self):
        cfg = {"judge": {"provider": "anthropic", "anthropic_model": "claude-haiku-4-5-20251001"}}
        _, label = _llm_judge.get_judge(cfg)
        self.assertEqual(label, "anthropic/claude-haiku-4-5-20251001")
        sys.modules["langchain_anthropic"].ChatAnthropic.assert_called_once_with(
            model="claude-haiku-4-5-20251001"
        )

    def test_ollama_with_base_url_passes_it_through(self):
        cfg = {"judge": {
            "provider": "ollama",
            "ollama_model": "llama3.2",
            "ollama_base_url": "http://192.0.2.10:11434",
        }}
        _, label = _llm_judge.get_judge(cfg)
        self.assertEqual(label, "ollama/llama3.2@http://192.0.2.10:11434")
        sys.modules["langchain_ollama"].ChatOllama.assert_called_once_with(
            model="llama3.2", base_url="http://192.0.2.10:11434"
        )

    def test_ollama_without_base_url_omits_kwarg(self):
        cfg = {"judge": {"provider": "ollama", "ollama_model": "llama3.2"}}
        _, label = _llm_judge.get_judge(cfg)
        self.assertEqual(label, "ollama/llama3.2@localhost")
        sys.modules["langchain_ollama"].ChatOllama.assert_called_once_with(model="llama3.2")

    def test_legacy_ragas_anthropic_still_works(self):
        # This is the regression case — old configs in the wild must keep working.
        cfg = {"ragas": {"llm_judge": "anthropic", "anthropic_model": "claude-haiku-4-5-20251001"}}
        _, label = _llm_judge.get_judge(cfg)
        self.assertEqual(label, "anthropic/claude-haiku-4-5-20251001")

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            _llm_judge.get_judge({"judge": {"provider": "openai"}})

    def test_default_provider_is_ollama(self):
        # Unset provider → ollama (the original ragas_eval default).
        _, label = _llm_judge.get_judge({})
        self.assertTrue(label.startswith("ollama/"))


class TestHealthCheck(unittest.TestCase):

    def test_anthropic_requires_api_key(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        ok, msg = _llm_judge.health_check({"judge": {"provider": "anthropic"}})
        self.assertFalse(ok)
        self.assertIn("ANTHROPIC_API_KEY", msg)

    def test_anthropic_with_api_key_passes(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake"
        try:
            ok, _ = _llm_judge.health_check({"judge": {"provider": "anthropic"}})
            self.assertTrue(ok)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_ollama_unreachable_returns_false(self):
        # Pick a port nothing's listening on; urllib will fail fast.
        cfg = {"judge": {"provider": "ollama", "ollama_base_url": "http://127.0.0.1:1"}}
        ok, msg = _llm_judge.health_check(cfg)
        self.assertFalse(ok)
        self.assertIn("ollama", msg.lower())

    def test_ollama_file_scheme_rejected(self):
        # file:// in ollama_base_url must never reach urlopen (CWE-939).
        cfg = {"judge": {"provider": "ollama", "ollama_base_url": "file:///etc/passwd"}}
        ok, msg = _llm_judge.health_check(cfg)
        self.assertFalse(ok)
        self.assertIn("unsafe scheme", msg)
        self.assertIn("file", msg)

    def test_ollama_custom_scheme_rejected(self):
        cfg = {"judge": {"provider": "ollama", "ollama_base_url": "ftp://internal.host:21"}}
        ok, msg = _llm_judge.health_check(cfg)
        self.assertFalse(ok)
        self.assertIn("unsafe scheme", msg)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestJudgeBlock))
    suite.addTests(loader.loadTestsFromTestCase(TestGetJudge))
    suite.addTests(loader.loadTestsFromTestCase(TestHealthCheck))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
