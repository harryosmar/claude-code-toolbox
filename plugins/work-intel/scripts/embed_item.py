"""Chunk, embed, and upsert items into ChromaDB for work-intel."""
import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
CHROMA_PATH = os.path.join(WORK_INTEL_HOME, "chroma")
CONFIG_PATH = os.path.join(WORK_INTEL_HOME, "config.json")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

CHUNK_SIZE = 512   # tokens (approx chars / 4)
CHUNK_OVERLAP = 64
URL_CONTEXT_WORDS = 10  # min words of context kept after a URL before chunk boundary
DEFAULT_MODEL = "jinaai/jina-embeddings-v5-text-small"

_URL_RE = re.compile(r'https?://\S+')
_URL_START_RE = re.compile(r'^https?://')

# Tier B PII redaction — sibling-imported lazily (Presidio pulls heavy deps).
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_config() -> dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _redact_pii_safely(body: str, language: str, cfg: dict[str, Any]) -> tuple[str, list[str]]:
    """Try to redact PII; never fail an embed because of a guard error.

    Returns (possibly-redacted body, list of detected entity types).
    Empty list = no redaction or guard disabled.
    """
    if os.environ.get("WORK_INTEL_GUARDS_INGEST", "").lower() == "disabled":
        return body, []
    if not cfg.get("guards", {}).get("ingest", {}).get("enabled", True):
        return body, []
    try:
        import _pii_classifier  # type: ignore[import-not-found]
        redacted, entities = _pii_classifier.redact(body, language=language, cfg=cfg)
        return redacted, [e.type for e in entities]
    except ImportError:
        # Presidio not installed — skip silently. Setup skill warns the user.
        return body, []
    except Exception as e:  # noqa: BLE001  (fail-open is the design)
        print(f"⚠️  PII redaction error (continuing without redaction): {e}", file=sys.stderr)
        return body, []


def load_model_name() -> str:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg.get("embedding", {}).get("model", DEFAULT_MODEL)
    return DEFAULT_MODEL


def extract_urls(text: str) -> list[str]:
    return _URL_RE.findall(text)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        end = min(i + chunk_size, len(words))
        # Don't end a chunk on a URL — extend forward so the URL has context after it.
        # This prevents a URL from being the last word of a chunk with no surrounding text.
        while end < len(words) and _URL_START_RE.match(words[end - 1]):
            end = min(end + URL_CONTEXT_WORDS, len(words))
        chunk = " ".join(words[i:end])
        chunks.append(chunk)
        i += (end - i) - overlap
        if i <= 0:
            break
    return chunks if chunks else [text]


def enrich_bare_url_body(body: str, title: str) -> str:
    """If the entire body is a single URL, prepend the title for semantic context."""
    stripped = body.strip()
    words = stripped.split()
    if len(words) == 1 and _URL_START_RE.match(stripped):
        prefix = title.strip()
        return f"{prefix} {stripped}".strip() if prefix else stripped
    return body


def item_id(base_id: str, chunk_idx: int) -> str:
    return f"{base_id}__chunk{chunk_idx}"


def embed_items(items: list[dict[str, Any]], collection_name: str) -> dict[str, Any]:
    import chromadb
    from sentence_transformers import SentenceTransformer

    model_name = load_model_name()
    model = SentenceTransformer(model_name, trust_remote_code=True)
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    cfg = _load_config()
    total_chunks = 0
    pii_redacted_total = 0
    failed: list[str] = []

    for item in items:
        try:
            body = item.get("body") or item.get("text") or ""
            if not body.strip():
                continue

            title = item.get("title") or ""

            # Tier B: PII redaction at ingest. Detects language from item body
            # (heuristic — only "en" vs "id"). Failures are logged and the
            # original body is kept (fail-open).
            try:
                import _pii_classifier  # type: ignore[import-not-found]
                lang = _pii_classifier.detect_language(body, default="en")
            except ImportError:
                lang = "en"
            body, pii_types = _redact_pii_safely(body, language=lang, cfg=cfg)
            pii_redacted_total += len(pii_types)

            # Enrich bare-URL messages with their title so the embedding has semantic weight
            body = enrich_bare_url_body(body, title)

            # Extract URLs before chunking and store as metadata so they survive chunk boundaries
            urls = extract_urls(body)

            chunks = chunk_text(body)
            base_id = item.get("id") or hashlib.sha256(body.encode()).hexdigest()[:16]
            metadata_base = {
                "source": item.get("source", ""),
                "type": item.get("type", ""),
                "title": title[:500],
                "url": item.get("url", ""),
                "owner": item.get("owner", ""),
                "priority": item.get("priority", "P2"),
                "status": item.get("status", "open"),
                "timestamp": item.get("timestamp", ""),
                "base_id": base_id,
                "urls": " ".join(urls)[:1000],  # all URLs in item, space-separated
                "pii_redacted_count": len(pii_types),
                "pii_types": ",".join(sorted(set(pii_types)))[:200],
            }
            extra = item.get("metadata", {})
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if isinstance(v, (str, int, float, bool)):
                        metadata_base[k] = v

            embeddings = model.encode(chunks, task="retrieval").tolist()

            collection.upsert(
                ids=[item_id(base_id, i) for i in range(len(chunks))],
                embeddings=embeddings,
                documents=chunks,
                metadatas=[{**metadata_base, "chunk_index": i} for i in range(len(chunks))],
            )
            total_chunks += len(chunks)
        except Exception as e:
            failed.append(item.get("id", "unknown"))
            print(f"⚠️  Failed to embed item {item.get('id')}: {e}", file=sys.stderr)

    return {
        "EMBED_RESULT": "FAILED" if len(failed) == len(items) else "OK",
        "ITEMS_EMBEDDED": len(items) - len(failed),
        "CHUNKS_STORED": total_chunks,
        "PII_REDACTED_TOTAL": pii_redacted_total,
        "FAILED_ITEMS": failed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--items-json", help="JSON array of items (from stdin if omitted)")
    parser.add_argument("--collection", default="items", help="ChromaDB collection name")
    # Single-item mode (legacy)
    parser.add_argument("--source", help="Source name")
    parser.add_argument("--id", help="Item ID")
    parser.add_argument("--title", help="Item title")
    parser.add_argument("--text", help="Item text body")
    parser.add_argument("--metadata", help="JSON metadata object")
    args = parser.parse_args()

    if args.items_json:
        items = json.loads(args.items_json)
    elif not sys.stdin.isatty():
        items = json.load(sys.stdin)
    elif args.text:
        items = [{
            "id": args.id or hashlib.sha256(args.text.encode()).hexdigest()[:16],
            "source": args.source or "manual",
            "title": args.title or "",
            "body": args.text,
            "metadata": json.loads(args.metadata) if args.metadata else {},
        }]
    else:
        print("No input provided", file=sys.stderr)
        sys.exit(1)

    result = embed_items(items, args.collection)
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
