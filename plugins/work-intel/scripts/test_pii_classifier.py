"""Unit + integration tests for _pii_classifier.

Lightweight tests run without Presidio installed (kill switch, language
heuristic, dataclass shape, empty-text fast path).

Integration tests gated on WORK_INTEL_RUN_INTEGRATION=1 since Presidio +
spaCy models are heavy (~12 MB en_core_web_sm + 11 MB xx_ent_wiki_sm).
"""
import os
import sys
import unittest

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import _pii_classifier  # noqa: E402

_RUN_INTEGRATION = os.environ.get("WORK_INTEL_RUN_INTEGRATION", "").lower() in ("1", "true", "yes")


class TestKillSwitch(unittest.TestCase):
    """The env-var disable path must short-circuit before any Presidio import."""

    def setUp(self):
        os.environ["WORK_INTEL_GUARDS_INGEST"] = "disabled"

    def tearDown(self):
        os.environ.pop("WORK_INTEL_GUARDS_INGEST", None)

    def test_detect_pii_returns_empty_when_disabled(self):
        result = _pii_classifier.detect_pii("Email me at jane@example.com", language="en")
        self.assertEqual(result, [])

    def test_redact_returns_text_unchanged_when_disabled(self):
        original = "Contact: jane@example.com"
        redacted, entities = _pii_classifier.redact(original, language="en")
        self.assertEqual(redacted, original)
        self.assertEqual(entities, [])

    def test_disabled_does_not_load_presidio(self):
        # Sanity: the kill switch must short-circuit before _ensure_engines
        # tries to import presidio. Reset globals and verify.
        _pii_classifier._ANALYZER = None
        _pii_classifier._ANONYMIZER = None
        _pii_classifier.detect_pii("anything", language="en")
        self.assertIsNone(_pii_classifier._ANALYZER)


class TestEmptyText(unittest.TestCase):

    def test_empty_string_returns_empty(self):
        self.assertEqual(_pii_classifier.detect_pii("", language="en"), [])

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(_pii_classifier.detect_pii("   \n\t  ", language="en"), [])

    def test_redact_empty_returns_unchanged(self):
        redacted, entities = _pii_classifier.redact("", language="en")
        self.assertEqual(redacted, "")
        self.assertEqual(entities, [])


class TestLanguageHeuristic(unittest.TestCase):

    def test_english_text_returns_en(self):
        text = "We discussed the proposal yesterday and it looks good for next week"
        self.assertEqual(_pii_classifier.detect_language(text), "en")

    def test_bahasa_text_returns_id(self):
        # Multiple Indonesian function words trigger the heuristic.
        text = "Saya akan kirim laporan untuk tim besok dengan data yang sudah lengkap"
        self.assertEqual(_pii_classifier.detect_language(text), "id")

    def test_short_text_returns_default(self):
        self.assertEqual(_pii_classifier.detect_language("hi", default="en"), "en")
        self.assertEqual(_pii_classifier.detect_language("", default="id"), "id")

    def test_one_marker_not_enough(self):
        # Single occurrence isn't enough — could be a borrowed word.
        text = "The yang dynasty ruled for many centuries in ancient China region"
        self.assertEqual(_pii_classifier.detect_language(text), "en")


class TestPIIEntityDataclass(unittest.TestCase):

    def test_pii_entity_is_immutable(self):
        e = _pii_classifier.PIIEntity(type="EMAIL_ADDRESS", start=0, end=10, score=0.9, text="x@y.com")
        with self.assertRaises(Exception):
            e.score = 0.5  # type: ignore[misc]  # frozen dataclass

    def test_pii_entity_fields(self):
        e = _pii_classifier.PIIEntity(type="PERSON", start=5, end=15, score=0.85, text="Jane Doe")
        self.assertEqual(e.type, "PERSON")
        self.assertEqual(e.start, 5)
        self.assertEqual(e.end, 15)
        self.assertEqual(e.score, 0.85)
        self.assertEqual(e.text, "Jane Doe")


@unittest.skipUnless(_RUN_INTEGRATION, "set WORK_INTEL_RUN_INTEGRATION=1 to run Presidio integration tests")
class TestPresidioIntegration(unittest.TestCase):
    """Real Presidio + spaCy models. Heavy — only runs in integration."""

    def setUp(self):
        # Make sure kill switch is off and analyzer is fresh.
        os.environ.pop("WORK_INTEL_GUARDS_INGEST", None)
        _pii_classifier._ANALYZER = None
        _pii_classifier._ANONYMIZER = None

    def test_detect_email_english(self):
        entities = _pii_classifier.detect_pii(
            "Please email me at jane.doe@example.com to follow up.",
            language="en",
        )
        types = [e.type for e in entities]
        self.assertIn("EMAIL_ADDRESS", types)

    def test_detect_phone_english(self):
        entities = _pii_classifier.detect_pii(
            "Call me at +1-555-123-4567 tomorrow afternoon.",
            language="en",
        )
        types = [e.type for e in entities]
        self.assertIn("PHONE_NUMBER", types)

    def test_redact_replaces_with_typed_marker(self):
        text = "Email jane@example.com today."
        redacted, entities = _pii_classifier.redact(text, language="en")
        self.assertNotIn("jane@example.com", redacted)
        self.assertIn("[REDACTED:EMAIL_ADDRESS]", redacted)
        self.assertGreaterEqual(len(entities), 1)

    def test_indonesian_nik_detected(self):
        # 16-digit NIK in a Bahasa context line.
        text = "Mohon kirim NIK: 3201234567890123 untuk verifikasi data."
        entities = _pii_classifier.detect_pii(text, language="id")
        types = [e.type for e in entities]
        self.assertIn("ID_NIK", types)

    def test_indonesian_npwp_detected(self):
        text = "NPWP perusahaan: 12.345.678.9-012.345 untuk faktur pajak."
        entities = _pii_classifier.detect_pii(text, language="id")
        types = [e.type for e in entities]
        self.assertIn("ID_NPWP", types)

    def test_bahasa_email_detected(self):
        text = "Hubungi saya di email budi@contoh.id atau telepon."
        entities = _pii_classifier.detect_pii(text, language="id")
        types = [e.type for e in entities]
        self.assertIn("EMAIL_ADDRESS", types)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestKillSwitch))
    suite.addTests(loader.loadTestsFromTestCase(TestEmptyText))
    suite.addTests(loader.loadTestsFromTestCase(TestLanguageHeuristic))
    suite.addTests(loader.loadTestsFromTestCase(TestPIIEntityDataclass))
    suite.addTests(loader.loadTestsFromTestCase(TestPresidioIntegration))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
