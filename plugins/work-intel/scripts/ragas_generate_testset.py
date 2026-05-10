"""Auto-generate a RAGAS Q&A testset by sampling from ChromaDB."""
import argparse
import json
import os
import random
import subprocess
import sys

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def sample_items(n: int) -> list[dict]:
    """Sample random items from ChromaDB via query_chroma.py using varied queries."""
    sample_queries = [
        "incident alert production",
        "meeting decision architecture",
        "task todo deadline",
        "error bug fix issue",
        "deploy release update",
        "plan roadmap feature",
    ]
    items = []
    seen_ids = set()
    per_query = max(1, (n * 2) // len(sample_queries))

    for q in sample_queries:
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "query_chroma.py"),
             "--q", q, "--top-k", str(per_query), "--output", "json"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            continue
        for item in json.loads(result.stdout):
            base_id = item.get("base_id") or item.get("title", "")[:40]
            if base_id not in seen_ids and len(item.get("text", "")) > 100:
                seen_ids.add(base_id)
                items.append(item)

    random.shuffle(items)
    return items[:n]


def generate_qa_pair(item: dict) -> dict | None:
    """Generate a Q&A pair from an item using the Claude Code session (no API cost)."""
    context = item.get("text", "")
    source = item.get("source", "")
    title = item.get("title", "")

    if len(context.strip()) < 50:
        return None

    # The agent calling this script will use Claude to generate Q&A pairs.
    # This function returns the item with context — generation happens externally.
    return {
        "context": context,
        "source": source,
        "title": title,
        "metadata": {k: v for k, v in item.items() if k not in {"text", "score"}},
        # Placeholders — to be filled by the calling agent using Claude
        "question": f"__GENERATE_QUESTION_FOR: {title[:80]}__",
        "ground_truth_answer": "__GENERATE_ANSWER__",
        "ground_truth_contexts": [context],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.path.join(WORK_INTEL_HOME, "config.json"))
    parser.add_argument("--output", default=os.path.join(WORK_INTEL_HOME, "ragas-testset.json"))
    parser.add_argument("--n-questions", type=int, default=40)
    args = parser.parse_args()

    print(f"Sampling {args.n_questions} items from ChromaDB...")
    items = sample_items(args.n_questions)

    if not items:
        print("❌ No items found in ChromaDB. Run /work-intel:setup first.", file=sys.stderr)
        sys.exit(1)

    testset = []
    for item in items:
        qa = generate_qa_pair(item)
        if qa:
            testset.append(qa)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(testset, f, indent=2)

    print(f"✅ Testset scaffold written: {args.output}")
    print(f"   {len(testset)} items need Q&A generation by the calling agent.")
    print("   The evaluate-rag skill will use Claude to fill in questions and answers.")


if __name__ == "__main__":
    main()
