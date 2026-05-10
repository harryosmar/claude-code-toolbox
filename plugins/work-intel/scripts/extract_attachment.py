"""Extract plain text from attachments using unstructured (auto-detects format)."""
import argparse
import hashlib
import json
import os
import sys

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".md", ".txt", ".eml", ".msg", ".html", ".htm", ".csv",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
}

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_config() -> dict:
    cfg_path = os.path.join(
        os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel")),
        "config.json",
    )
    if not os.path.exists(cfg_path):
        return {}
    try:
        with open(cfg_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _redact_attachment_text(text: str) -> tuple[str, list[str]]:
    """Tier B: redact PII from extracted attachment text. Fail-open."""
    if os.environ.get("WORK_INTEL_GUARDS_INGEST", "").lower() == "disabled":
        return text, []
    cfg = _load_config()
    if not cfg.get("guards", {}).get("ingest", {}).get("enabled", True):
        return text, []
    try:
        import _pii_classifier  # type: ignore[import-not-found]
        lang = _pii_classifier.detect_language(text, default="en")
        redacted, entities = _pii_classifier.redact(text, language=lang, cfg=cfg)
        return redacted, [e.type for e in entities]
    except ImportError:
        return text, []
    except Exception as e:  # noqa: BLE001  (fail-open by design)
        print(f"⚠️  PII redaction error on attachment (continuing): {e}", file=sys.stderr)
        return text, []


def extract(filepath: str) -> dict:
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return {"error": f"Unsupported file type: {ext}", "text": "", "metadata": {}}

    try:
        from unstructured.partition.auto import partition
        elements = partition(filename=filepath)
        text = "\n".join(e.text for e in elements if hasattr(e, "text") and e.text)
    except ImportError:
        return {
            "error": "unstructured not installed. Run: pip install 'unstructured[all-docs]'",
            "text": "",
            "metadata": {},
        }
    except Exception as e:
        return {"error": str(e), "text": "", "metadata": {}}

    redacted_text, pii_types = _redact_attachment_text(text)
    sha = hashlib.sha256(text.encode()).hexdigest()[:12]
    return {
        "text": redacted_text,
        "metadata": {
            "file": filepath,
            "filename": os.path.basename(filepath),
            "ext": ext,
            "size_bytes": os.path.getsize(filepath),
            "content_hash": sha,
            "pii_redacted_count": len(pii_types),
            "pii_types": sorted(set(pii_types)),
        },
        "error": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to file to extract")
    parser.add_argument("--output-json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = extract(args.file)

    if result.get("error"):
        print(f"❌ {result['error']}", file=sys.stderr)
        if args.output_json:
            print(json.dumps(result))
        sys.exit(1)

    if args.output_json:
        print(json.dumps(result))
    else:
        print(result["text"])


if __name__ == "__main__":
    main()
