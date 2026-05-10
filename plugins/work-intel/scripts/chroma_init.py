"""Initialize or verify ChromaDB collections for work-intel."""
import argparse
import os
import sys

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
CHROMA_PATH = os.path.join(WORK_INTEL_HOME, "chroma")

COLLECTIONS = {
    "items": "All ingested items from connected sources (Jira, GitLab, Gmail, WA, Telegram, Truewatch)",
    "documents": "Manually ingested documents and Google Drive files",
}


def get_client():
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_PATH)


def init_collections(client):
    for name, description in COLLECTIONS.items():
        client.get_or_create_collection(
            name=name,
            metadata={"description": description, "hnsw:space": "cosine"},
        )
        print(f"✅ Collection '{name}' ready")


def verify_collections(client):
    all_ok = True
    for name in COLLECTIONS:
        try:
            col = client.get_collection(name)
            count = col.count()
            print(f"✅ {name}: {count} chunks")
        except Exception as e:
            print(f"❌ {name}: {e}")
            all_ok = False
    return all_ok


def check_embedding_model():
    import json
    config_path = os.path.join(WORK_INTEL_HOME, "config.json")
    model = "jinaai/jina-embeddings-v5-text-small"
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        model = cfg.get("embedding", {}).get("model", model)
    print(f"Checking embedding model: {model}")
    from sentence_transformers import SentenceTransformer
    SentenceTransformer(model, trust_remote_code=True)
    print(f"✅ Embedding model ready: {model}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Verify existing collections")
    args = parser.parse_args()

    os.makedirs(CHROMA_PATH, exist_ok=True)

    try:
        client = get_client()
    except ImportError:
        print("❌ chromadb not installed. Run: pip install chromadb")
        sys.exit(1)

    if args.verify:
        ok = verify_collections(client)
        sys.exit(0 if ok else 1)

    init_collections(client)
    check_embedding_model()
    print(f"\n✅ ChromaDB initialized at {CHROMA_PATH}")


if __name__ == "__main__":
    main()
