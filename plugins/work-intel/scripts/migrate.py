"""Post-restore migration: verify model pin, test vectors, detect missing credentials."""
import json
import os
import sys

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
CONFIG_PATH = os.path.join(WORK_INTEL_HOME, "config.json")
CHROMA_PATH = os.path.join(WORK_INTEL_HOME, "chroma")
DEFAULT_MODEL = "jinaai/jina-embeddings-v5-text-small"

CREDENTIAL_CHECKS = {
    "Jira":         os.path.expanduser("~/.claude/secrets/jira.env"),
    "Telegram":     os.path.expanduser("~/.claude/secrets/telegram.env"),
    "Truewatch":    None,  # via MCP config, always assumed present
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️  config.json not found at {CONFIG_PATH}", file=sys.stderr)
        return {}
    with open(CONFIG_PATH) as f:
        return json.load(f)


def check_model(model_name: str) -> bool:
    print(f"Downloading/verifying embedding model: {model_name}")
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name, trust_remote_code=True)
        test_embed = model.encode(["test vector"])
        assert test_embed.shape[1] > 0
        print(f"✅ Embedding model ready: {model_name} (dim={test_embed.shape[1]})")
        return True
    except ImportError:
        print("❌ sentence-transformers not installed. Run: pip install sentence-transformers", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Model verification failed: {e}", file=sys.stderr)
        return False


def verify_chroma() -> tuple[bool, dict]:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        counts = {}
        for name in ["items", "documents"]:
            try:
                col = client.get_collection(name)
                counts[name] = col.count()
            except Exception:
                counts[name] = None
        return True, counts
    except ImportError:
        print("❌ chromadb not installed. Run: pip install chromadb", file=sys.stderr)
        return False, {}
    except Exception as e:
        print(f"❌ ChromaDB error: {e}", file=sys.stderr)
        return False, {}


def test_vector_query(model_name: str) -> bool:
    try:
        from sentence_transformers import SentenceTransformer
        import chromadb
        model = SentenceTransformer(model_name)
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        col = client.get_collection("items")
        if col.count() == 0:
            print("ℹ️  items collection is empty — skipping query test")
            return True
        emb = model.encode(["test query"]).tolist()
        col.query(query_embeddings=emb, n_results=1)
        print("✅ Vector query test passed")
        return True
    except Exception as e:
        print(f"❌ Vector query test failed: {e}", file=sys.stderr)
        return False


def check_credentials() -> dict:
    status = {}
    for name, path in CREDENTIAL_CHECKS.items():
        if path is None:
            status[name] = "assumed_ok"
        elif os.path.exists(path):
            status[name] = "ok"
        else:
            status[name] = "missing"

    # OAuth-based (Gmail, Google Drive) — token files may be device-bound
    for name in ["Gmail", "Google Drive"]:
        token_path = os.path.expanduser(f"~/.config/{name.lower().replace(' ', '-')}/token.json")
        if os.path.exists(token_path):
            status[name] = "ok (verify manually — OAuth may be device-bound)"
        else:
            status[name] = "missing (re-auth required)"

    # WAHA — check config.json for waha.session
    cfg = load_config()
    if cfg.get("waha", {}).get("targets"):
        status["WAHA"] = "config present (WAHA Docker must be running)"
    else:
        status["WAHA"] = "not configured"

    return status


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-intel-home", default=WORK_INTEL_HOME)
    args = parser.parse_args()

    global WORK_INTEL_HOME, CONFIG_PATH, CHROMA_PATH
    WORK_INTEL_HOME = args.work_intel_home
    CONFIG_PATH = os.path.join(WORK_INTEL_HOME, "config.json")
    CHROMA_PATH = os.path.join(WORK_INTEL_HOME, "chroma")

    print(f"Migrating work-intel at: {WORK_INTEL_HOME}\n")
    cfg = load_config()
    model_name = cfg.get("embedding", {}).get("model", DEFAULT_MODEL)

    model_ok = check_model(model_name)
    chroma_ok, counts = verify_chroma()
    query_ok = test_vector_query(model_name) if chroma_ok else False
    creds = check_credentials()

    # Optional: count restored RAGAS + security-audit reports (tolerates absence).
    ragas_count = 0
    audit_count = 0
    for sub, label in (("ragas-reports", "ragas"), ("security-audit", "audit")):
        d = os.path.join(WORK_INTEL_HOME, sub)
        if os.path.isdir(d):
            n = sum(1 for f in os.listdir(d)
                    if f.endswith(".json") and not f.startswith("_"))
            if label == "ragas":
                ragas_count = n
            else:
                audit_count = n

    print("\n" + "=" * 50)
    print("Migration Report")
    print("=" * 50)
    print(f"Embedding model: {model_name}  {'✅' if model_ok else '❌'}")
    print("\nChromaDB collections:")
    for name, count in counts.items():
        if count is None:
            print(f"  {name}: ❌ not found")
        else:
            print(f"  {name}: {count} chunks  ✅")
    print(f"\nVector query test: {'✅' if query_ok else '❌'}")
    print(f"\nReports restored: ragas={ragas_count}, security-audit={audit_count}")
    print("\nCredentials:")
    for name, status in creds.items():
        icon = "✅" if status.startswith("ok") or status.startswith("assumed") else "⚠️"
        print(f"  {name}: {icon}  {status}")

    needs_reauth = [n for n, s in creds.items() if "missing" in s or "re-auth" in s]
    if needs_reauth:
        print(f"\n⚠️  Re-auth required for: {', '.join(needs_reauth)}")
        print("   Run: /work-intel:setup and select those sources")

    if model_ok and chroma_ok:
        print("\n✅ Migration complete. Run /work-intel:morning-briefing to resume.")
    else:
        print("\n❌ Migration has errors — check output above before proceeding.")
        sys.exit(1)


if __name__ == "__main__":
    main()
