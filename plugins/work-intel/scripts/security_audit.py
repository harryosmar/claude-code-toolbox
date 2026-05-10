"""Tier C batch security audit — replays deepteam guards over a sample of
ChromaDB docs, recent skill outputs, and recent user queries.

Mirrors ragas_eval.py's report shape and regression-detection pattern.

Outputs:
  $WORK_INTEL_HOME/security-audit/security-audit-<UTC-ts>.json   (machine)
  $WORK_INTEL_HOME/security-audit/security-audit-<UTC-ts>.md     (human)
  $WORK_INTEL_HOME/security-audit/_raw/<UTC-ts>/                 (raw text, excluded from backup)

Reads judge config from `judge.*` (with legacy `ragas.*` fallback) via
_llm_judge.get_judge. Wraps it as a DeepEvalBaseLLM via _deepeval_adapter
so deepteam.guardrails.Guardrails can use it.

Honors `WORK_INTEL_GUARDRAILS=disabled` env var (returns immediately).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORK_INTEL_HOME = os.environ.get("WORK_INTEL_HOME", os.path.expanduser("~/.work-intel"))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_PATH = os.path.join(WORK_INTEL_HOME, "chroma")
DEFAULT_CONFIG = os.path.join(WORK_INTEL_HOME, "config.json")
DEFAULT_REPORT_DIR = os.path.join(WORK_INTEL_HOME, "security-audit")
REGRESSION_THRESHOLD = 0.05

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def load_config(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ Malformed config {path}: {e}", file=sys.stderr)
            sys.exit(1)


def sample_chroma_docs(sample_size: int) -> list[dict[str, Any]]:
    """Sample up to `sample_size` docs from every ChromaDB collection."""
    try:
        import chromadb
    except ImportError:
        print("❌ chromadb not installed. Run: pip install chromadb", file=sys.stderr)
        sys.exit(1)

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collections = client.list_collections()
    docs: list[dict[str, Any]] = []
    for col in collections:
        try:
            coll = client.get_collection(col.name)
            count = coll.count()
            if count == 0:
                continue
            # Pull all then random.sample — collections rarely exceed 100k items.
            page = coll.get(limit=min(count, 10_000), include=["documents", "metadatas"])
            ids = page.get("ids") or []
            documents = page.get("documents") or []
            metadatas = page.get("metadatas") or []
            for did, doc, meta in zip(ids, documents, metadatas):
                if not doc:
                    continue
                docs.append({
                    "id": did,
                    "text": doc,
                    "metadata": meta or {},
                    "collection": col.name,
                })
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  Skipping collection {col.name}: {e}", file=sys.stderr)
    if len(docs) > sample_size:
        random.seed(42)  # reproducible sample for diff vs prior reports
        docs = random.sample(docs, sample_size)
    return docs


def load_recent_jsonl(path: str, limit: int) -> list[dict[str, Any]]:
    """Read the last `limit` lines of a JSONL log; tolerate missing/empty file."""
    if not os.path.exists(path):
        return []
    rows: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def load_recent_outputs(dir_path: str, limit: int) -> list[dict[str, Any]]:
    """Glob *.md files in output-log/ sorted by mtime desc, take the latest `limit`."""
    if not os.path.isdir(dir_path):
        return []
    files = sorted(
        Path(dir_path).glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    rows = []
    for p in files:
        try:
            rows.append({
                "id": p.stem,
                "markdown": p.read_text(),
                "mtime": p.stat().st_mtime,
            })
        except OSError:
            continue
    return rows


def make_guardrails(cfg: dict[str, Any]):
    """Build a deepteam Guardrails instance using the shared judge."""
    try:
        from deepteam.guardrails import Guardrails
        from deepteam.guardrails.guards import (
            CybersecurityGuard,
            IllegalGuard,
            PrivacyGuard,
            PromptInjectionGuard,
            ToxicityGuard,
            TopicalGuard,
        )
    except ImportError as e:
        raise ImportError(
            "deepteam not installed. Run: pip install deepteam"
        ) from e

    guard_names = cfg.get("security_audit", {}).get("guards", [
        "PrivacyGuard", "PromptInjectionGuard", "ToxicityGuard",
        "IllegalGuard", "TopicalGuard", "CybersecurityGuard",
    ])
    registry = {
        "PrivacyGuard": PrivacyGuard,
        "PromptInjectionGuard": PromptInjectionGuard,
        "ToxicityGuard": ToxicityGuard,
        "IllegalGuard": IllegalGuard,
        "TopicalGuard": TopicalGuard,
        "CybersecurityGuard": CybersecurityGuard,
    }

    input_guards: list[Any] = []
    output_guards: list[Any] = []
    for name in guard_names:
        cls = registry.get(name)
        if cls is None:
            print(f"⚠️  Unknown guard: {name}; skipping", file=sys.stderr)
            continue
        # PromptInjectionGuard, TopicalGuard are input-side; rest are output-side.
        if name in ("PromptInjectionGuard", "TopicalGuard"):
            input_guards.append(cls())
        else:
            output_guards.append(cls())
        if name == "CybersecurityGuard":
            input_guards.append(cls())  # also runs as input-side for query path

    from _deepeval_adapter import make_judge  # type: ignore[import-not-found]
    judge = make_judge(cfg)
    return Guardrails(
        input_guards=input_guards,
        output_guards=output_guards,
        evaluation_model=judge,  # type: ignore[arg-type]
    )


def replay_inputs(guardrails, queries: list[str]) -> list[dict[str, Any]]:
    """Run guard_input over each query; collect breach verdicts."""
    results = []
    for q in queries:
        try:
            r = guardrails.guard_input(input=q)
            results.append(_serialize_result(r, "input", q))
        except Exception as e:  # noqa: BLE001
            results.append({"breached": False, "error": str(e), "scope": "input", "preview": q[:120]})
    return results


def replay_outputs(guardrails, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Run guard_output over each (input, output) pair; collect breach verdicts."""
    results = []
    for inp, out in pairs:
        try:
            r = guardrails.guard_output(input=inp, output=out)
            results.append(_serialize_result(r, "output", out))
        except Exception as e:  # noqa: BLE001
            results.append({"breached": False, "error": str(e), "scope": "output", "preview": out[:120]})
    return results


def _serialize_result(r: Any, scope: str, preview_src: str) -> dict[str, Any]:
    """Convert a deepteam GuardResult to a JSON-friendly dict."""
    verdicts = getattr(r, "verdicts", []) or []
    serialized = []
    for v in verdicts:
        serialized.append({
            "name": getattr(v, "name", "unknown"),
            "safety_level": getattr(v, "safety_level", "uncertain"),
            "score": getattr(v, "score", None),
            "reason": (getattr(v, "reason", "") or "")[:300],
        })
    return {
        "scope": scope,
        "breached": bool(getattr(r, "breached", False)),
        "verdicts": serialized,
        "preview": preview_src[:200],
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-guard pass/fail tally."""
    counts: dict[str, dict[str, int]] = {}
    for r in results:
        for v in r.get("verdicts", []):
            name = v["name"]
            level = v.get("safety_level", "uncertain")
            counts.setdefault(name, {"safe": 0, "unsafe": 0, "uncertain": 0})
            counts[name][level] = counts[name].get(level, 0) + 1
    total = len(results)
    breached = sum(1 for r in results if r.get("breached"))
    return {
        "total": total,
        "breached": breached,
        "pass_rate": round((total - breached) / total, 4) if total else 1.0,
        "per_guard": counts,
    }


def load_prior_report(reports_dir: str, current_path: str) -> dict[str, Any] | None:
    """Mirror of ragas_eval.load_prior_report — returns the most-recent prior report."""
    if not os.path.isdir(reports_dir):
        return None
    reports = sorted(
        [f for f in os.listdir(reports_dir)
         if f.startswith("security-audit-") and f.endswith(".json")],
        reverse=True,
    )
    for r in reports:
        full = os.path.join(reports_dir, r)
        if full != current_path:
            try:
                with open(full) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    return None


def format_delta(current: float, prior: float | None) -> str:
    if prior is None:
        return "(no prior)"
    delta = current - prior
    sign = "+" if delta >= 0 else ""
    regressed = delta < -REGRESSION_THRESHOLD
    icon = "⚠️" if regressed else "✅"
    tag = " — REGRESSION" if regressed else ""
    return f"{icon} ({sign}{delta:.2f} vs prior{tag})"


def write_markdown_report(report: dict[str, Any], prior: dict[str, Any] | None, path: str) -> None:
    sections: list[str] = []
    sections.append(f"# Security Audit — {report['timestamp'][:10]}")
    sections.append("")
    sections.append(f"**Judge:** `{report['judge']}`")
    sections.append(f"**Sample size:** {report['sample_size']} ChromaDB docs, "
                    f"{report['n_queries_replayed']} queries, "
                    f"{report['n_outputs_replayed']} outputs")
    sections.append("")

    for scope_label, scope_key in (
        ("ChromaDB sample", "chroma"),
        ("Replayed queries (input guards)", "queries"),
        ("Replayed outputs (output guards)", "outputs"),
    ):
        agg = report["aggregates"].get(scope_key, {})
        if not agg.get("total"):
            continue
        prior_pass = (prior or {}).get("aggregates", {}).get(scope_key, {}).get("pass_rate")
        sections.append(f"## {scope_label}")
        sections.append("")
        sections.append(f"- Total: {agg['total']}")
        sections.append(f"- Breached: {agg['breached']}")
        sections.append(f"- Pass rate: {agg['pass_rate']:.2%}  {format_delta(agg['pass_rate'], prior_pass)}")
        sections.append("")
        if agg.get("per_guard"):
            sections.append("| Guard | Safe | Unsafe | Uncertain |")
            sections.append("|---|---:|---:|---:|")
            for gname, c in sorted(agg["per_guard"].items()):
                sections.append(f"| {gname} | {c.get('safe', 0)} | {c.get('unsafe', 0)} | {c.get('uncertain', 0)} |")
            sections.append("")

    # Top breaches
    breaches = [r for scope in ("chroma", "queries", "outputs")
                for r in report["raw_results"].get(scope, []) if r.get("breached")]
    if breaches:
        sections.append("## Top breaches (up to 10)")
        sections.append("")
        for b in breaches[:10]:
            reasons = "; ".join(v.get("reason", "") for v in b.get("verdicts", []) if v.get("safety_level") == "unsafe")
            sections.append(f"- **{b['scope']}** — {b.get('preview', '')[:120]!r} → {reasons[:200]}")
        sections.append("")

    Path(path).write_text("\n".join(sections))


def main() -> None:
    if os.environ.get("WORK_INTEL_GUARDRAILS", "").lower() == "disabled":
        print("WORK_INTEL_GUARDRAILS=disabled — skipping security audit.", file=sys.stderr)
        sys.exit(0)

    parser = argparse.ArgumentParser(description="Security audit (Tier C batch).")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--sample-size", type=int, default=None,
                        help="Override security_audit.sample_size from config.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    audit_cfg = cfg.get("security_audit", {})
    sample_size = args.sample_size or audit_cfg.get("sample_size", 200)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(args.output_dir, exist_ok=True)
    raw_dir = os.path.join(args.output_dir, "_raw", timestamp)
    os.makedirs(raw_dir, exist_ok=True)
    json_path = os.path.join(args.output_dir, f"security-audit-{timestamp}.json")
    md_path = os.path.join(args.output_dir, f"security-audit-{timestamp}.md")

    # 1. Sample ChromaDB
    print(f"Sampling up to {sample_size} ChromaDB docs...", flush=True)
    chroma_docs = sample_chroma_docs(sample_size)
    print(f"  got {len(chroma_docs)} docs", flush=True)

    # 2. Recent queries + outputs
    queries = [r.get("query", "") for r in load_recent_jsonl(
        os.path.join(WORK_INTEL_HOME, "query-log.jsonl"), 200) if r.get("query")]
    outputs = load_recent_outputs(os.path.join(WORK_INTEL_HOME, "output-log"), 50)
    print(f"  {len(queries)} replayed queries, {len(outputs)} replayed outputs", flush=True)

    # 3. Build guardrails
    print("Building guardrails (this lazy-loads deepteam + judge)...", flush=True)
    try:
        guardrails = make_guardrails(cfg)
    except ImportError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # Get judge label for the report header
    try:
        judge_label = guardrails.evaluation_model.get_model_name()  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        judge_label = "unknown"

    # 4. Replay
    chroma_results = replay_inputs(guardrails, [d["text"] for d in chroma_docs])
    query_results = replay_inputs(guardrails, queries)
    output_results = replay_outputs(guardrails, [(o["id"], o["markdown"]) for o in outputs])

    # 5. Save raw text (excluded from backup) for incident triage
    if chroma_docs:
        with open(os.path.join(raw_dir, "chroma_docs.json"), "w") as f:
            json.dump([{"id": d["id"], "text": d["text"][:5000]} for d in chroma_docs], f, indent=2)
    if queries:
        with open(os.path.join(raw_dir, "queries.json"), "w") as f:
            json.dump(queries, f, indent=2)
    if outputs:
        with open(os.path.join(raw_dir, "outputs.json"), "w") as f:
            json.dump([{"id": o["id"], "preview": o["markdown"][:1000]} for o in outputs], f, indent=2)

    # 6. Build report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "judge": judge_label,
        "sample_size": len(chroma_docs),
        "n_queries_replayed": len(query_results),
        "n_outputs_replayed": len(output_results),
        "aggregates": {
            "chroma": aggregate(chroma_results),
            "queries": aggregate(query_results),
            "outputs": aggregate(output_results),
        },
        "raw_results": {
            "chroma": chroma_results,
            "queries": query_results,
            "outputs": output_results,
        },
    }

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)

    prior = load_prior_report(args.output_dir, json_path)
    write_markdown_report(report, prior, md_path)

    print(f"\nReport saved: {md_path}", flush=True)

    # 7. Regression exit code (mirrors ragas_eval.py)
    has_regression = False
    if prior:
        for scope in ("chroma", "queries", "outputs"):
            cur = report["aggregates"].get(scope, {}).get("pass_rate")
            pri = prior.get("aggregates", {}).get(scope, {}).get("pass_rate")
            if cur is not None and pri is not None and cur - pri < -REGRESSION_THRESHOLD:
                has_regression = True
                break
    if has_regression:
        sys.exit(2)


if __name__ == "__main__":
    main()
