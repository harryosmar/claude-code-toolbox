"""Semantic search over work-intel ChromaDB collections."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
CHROMA_PATH = os.path.join(WORK_INTEL_HOME, "chroma")
CONFIG_PATH = os.path.join(WORK_INTEL_HOME, "config.json")
DEFAULT_MODEL = "jinaai/jina-embeddings-v5-text-small"


def load_model_name() -> str:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg.get("embedding", {}).get("model", DEFAULT_MODEL)
    return DEFAULT_MODEL


def parse_since(since: str) -> Optional[str]:
    """Convert relative time like '24h', '7d', '30d', '1m' to ISO8601 cutoff."""
    now = datetime.now(timezone.utc)
    if since.endswith("h"):
        dt = now - timedelta(hours=int(since[:-1]))
    elif since.endswith("d"):
        dt = now - timedelta(days=int(since[:-1]))
    elif since.endswith("m"):
        dt = now - timedelta(days=int(since[:-1]) * 30)
    else:
        return since
    return dt.isoformat()


def build_where(sources: Optional[str], priority: Optional[str], since: Optional[str]) -> Optional[dict]:
    conditions: list[dict] = []

    if sources:
        src_list = [s.strip() for s in sources.split(",")]
        if len(src_list) == 1:
            conditions.append({"source": {"$eq": src_list[0]}})
        else:
            conditions.append({"source": {"$in": src_list}})

    if priority:
        p_list = [p.strip() for p in priority.split(",")]
        if len(p_list) == 1:
            conditions.append({"priority": {"$eq": p_list[0]}})
        else:
            conditions.append({"priority": {"$in": p_list}})

    # Note: timestamp is stored as ISO string; ChromaDB $gte requires numeric.
    # since-filtering is applied as Python post-filter in query() instead.

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def query(
    query_text: str,
    collection_name: str,
    top_k: int,
    sources: Optional[str],
    priority: Optional[str],
    since: Optional[str],
) -> list[dict[str, Any]]:
    import chromadb
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(load_model_name(), trust_remote_code=True)
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection(collection_name)

    embedding = model.encode([query_text], task="retrieval")[0].tolist()
    where = build_where(sources, priority, since)

    # Fetch more when since-filtering to ensure we get enough after Python post-filter
    fetch_k = min(top_k * 5 if since else top_k, collection.count() or 1)

    kwargs: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": fetch_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    cutoff_iso: Optional[str] = parse_since(since) if since else None

    # ChromaDB's QueryResult exposes documents/metadatas/distances as Optional;
    # default each to [[]] so the zip below stays safe on an empty collection.
    docs = results.get("documents") or [[]]
    metas = results.get("metadatas") or [[]]
    dists = results.get("distances") or [[]]

    items: list[dict[str, Any]] = []
    for doc, meta, dist in zip(docs[0], metas[0], dists[0]):
        # Post-filter by timestamp (ISO string comparison is correct for UTC ISO8601)
        if cutoff_iso and str(meta.get("timestamp", "")) < cutoff_iso:
            continue
        items.append({
            "text": doc,
            "score": round(1 - dist, 4),
            **meta,
        })
    return items[:top_k]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--q", help="Query text")
    parser.add_argument("--similar-to", dest="similar_to", help="Find items similar to this ID or text")
    parser.add_argument("--collection", default="items")
    parser.add_argument("--top-k", dest="top_k", type=int, default=10)
    parser.add_argument("--source", help="Comma-separated source filter")
    parser.add_argument("--priority", help="Comma-separated priority filter (P0,P1,P2,P3)")
    parser.add_argument("--since", help="Time filter: 24h, 7d, 30d, 1m, or ISO8601")
    parser.add_argument("--output", choices=["json", "text"], default="text")
    args = parser.parse_args()

    query_text = args.q or args.similar_to
    if not query_text:
        print("Provide --q or --similar-to", file=sys.stderr)
        sys.exit(1)

    results = query(
        query_text=query_text,
        collection_name=args.collection,
        top_k=args.top_k,
        sources=args.source,
        priority=args.priority,
        since=args.since,
    )

    if args.output == "json":
        print(json.dumps(results, indent=2))
    else:
        for i, item in enumerate(results, 1):
            print(f"\n[{i}] score={item['score']} | {item.get('source','')} | {item.get('priority','')} | {item.get('timestamp','')}")
            print(f"    {item.get('title','')}")
            print(f"    {item['text'][:200]}...")


if __name__ == "__main__":
    main()
