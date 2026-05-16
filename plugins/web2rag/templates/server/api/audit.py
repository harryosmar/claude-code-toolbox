"""POST /audit — runs RAGAS (quality) and DeepTeam (security/red-team).

Two-tool architecture (see plugin CLAUDE.md):
  - RAGAS for RAG-quality metrics (Faithfulness, AnswerRelevancy,
    ContextPrecision, ContextRecall) + automatic Q&A test-set generation.
  - DeepTeam for red-team simulators (jailbreak / prompt-injection / bias /
    toxicity / prompt-leakage) — picks up where the in-process guards leave
    off, attacking the live chat path.

Live PII / toxicity / hallucination scoring is NOT a separate audit mode
— that's enforced on every chat call by guard_query + guard_output. The
audit's `guard_replay` section reports those guards' hit rates against
the test set so operators can tune thresholds.

Body schema:
  mode:        "quality" | "redteam" | "all"
  judge:       "anthropic-haiku"
             | "ollama:<model>"
             | "openai-compat:<model>"            (uses OLLAMA_URL)
             | "custom-openai:<url>:<model>"      (url passed inline)
  testset_size: int (default 50)
  output:      "html" | "md" | "json" (default html)
  locales:     ["en", "id"] (default both)
  site_id:     optional
  regenerate_testset: bool (default false)

Returns: { report_path, summary }
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from eval.judges.factory import create_judge
from eval.runners import capability as capability_runner
from eval.runners import locale_probes as locale_probes_runner
from eval.runners import quality as quality_runner
from eval.runners import redteam as redteam_runner
from eval.runners import testset as testset_runner
from eval.runners.guard_replay import replay_query
from server.store.feedback import make_store as make_feedback_store
from server.config import settings
from server.llm.claude import stream_chat
from server.retrieval.prompt import SYSTEM_PROMPT, build_documents
from server.retrieval.rerank import rerank
from server.retrieval.search import hybrid_search

log = logging.getLogger(__name__)
router = APIRouter()

REPORTS_DIR = Path("reports")
Mode = Literal["quality", "capability", "redteam", "all"]


class AuditPayload(BaseModel):
    mode: Mode
    judge: str = "anthropic-haiku"
    testset_size: int = Field(default_factory=lambda: settings.testset_default_size, ge=1, le=500)
    output: Literal["html", "md", "json"] = "html"
    locales: list[str] = Field(default_factory=lambda: ["en", "id"])
    site_id: str | None = None
    regenerate_testset: bool = False


@router.post("/audit")
async def audit(payload: AuditPayload) -> dict[str, Any]:
    judge = create_judge(payload.judge)

    # Bridge: how a runner asks the bot a question (no HTTP — direct call).
    async def answer_for(question: str) -> dict[str, Any]:
        where = {"site_id": payload.site_id} if payload.site_id else None
        candidates = await asyncio.to_thread(
            hybrid_search, question, top_k=settings.retrieve_top_k, where=where
        )
        hits = await asyncio.to_thread(
            rerank, question, candidates, top_n=settings.rerank_top_n
        )
        documents = build_documents(hits)
        text = ""
        async for evt in stream_chat(
            user_message=question,
            history=[],
            documents=documents,
            system_prompt=SYSTEM_PROMPT,
            hits=hits,
        ):
            if evt["type"] == "token":
                text += evt["text"]
        return {"answer": text, "contexts": [h.text for h in hits]}

    sections: dict[str, Any] = {}

    samples: list[dict[str, Any]] = []
    if payload.mode in ("quality", "capability", "all") and payload.site_id:
        samples = testset_runner.load_or_generate(
            site_id=payload.site_id,
            judge=judge,
            size=payload.testset_size,
            locales=payload.locales,
            regenerate=payload.regenerate_testset,
        )

    if payload.mode in ("quality", "all"):
        sections["quality"] = await quality_runner.run(judge=judge, samples=samples, answer_for=answer_for)
    if payload.mode in ("capability", "all"):
        sections["capability"] = await capability_runner.run(judge=judge, samples=samples, answer_for=answer_for)
        # Production thumbs-up/down is the source of truth for capability —
        # surface it in the report so auto-eval and human signal sit side-by-side.
        sections["capability"]["production_feedback"] = make_feedback_store().summary()
    if payload.mode in ("redteam", "all"):
        rt = await redteam_runner.run(judge=judge, answer_for=answer_for)
        sections["redteam"] = rt
        # Replay guard_query over the testset prompts so operators see
        # how often the cheap in-process guards already short-circuit
        # adversarial inputs before DeepTeam's simulators even fire.
        sample_prompts = [s.get("question", "") for s in samples if s.get("question")]
        if sample_prompts:
            sections["guard_replay"] = {
                "query": replay_query(sample_prompts, site_id=payload.site_id),
            }
        # Bilingual locale probes — hand-curated regression set covering
        # "guards must not false-refuse legit ID queries" and "bot must
        # mirror the user's language". Complements DeepTeam's auto-attack
        # simulators which are English-by-default. Free to run (no LLM
        # judge — deterministic checks), so we always include in redteam
        # mode and "all" mode.
        sections["locale_probes"] = await locale_probes_runner.run(
            answer_for=answer_for,
            site_id=payload.site_id,
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": payload.mode,
        "judge": payload.judge,
        "site_id": payload.site_id,
        "locales": payload.locales,
        "testset_size": len(samples),
        "sections": sections,
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = REPORTS_DIR / f"audit_{ts}.{payload.output}"

    if payload.output == "json":
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    elif payload.output == "md":
        path.write_text(_render_md(report), encoding="utf-8")
    else:  # html
        path.write_text(_render_html(report), encoding="utf-8")

    return {"report_path": str(path), "summary": _condense(sections)}


def _condense(sections: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "quality" in sections:
        out["quality"] = sections["quality"].get("summary", {})
    if "capability" in sections:
        out["capability"] = sections["capability"].get("summary", {})
        out["capability"]["production_feedback"] = sections["capability"].get("production_feedback", {})
    if "redteam" in sections:
        out["redteam"] = sections["redteam"].get("summary", {})
    if "locale_probes" in sections:
        out["locale_probes"] = sections["locale_probes"].get("summary", {})
    return out


def _render_md(report: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# web2rag audit — {report['generated_at']}",
        "",
        f"- mode: `{report['mode']}`",
        f"- judge: `{report['judge']}`",
        f"- site_id: `{report.get('site_id') or '(all sites)'}`",
        f"- testset_size: {report['testset_size']}",
        "",
    ]
    for name, section in report["sections"].items():
        lines.append(f"## {name}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(section.get("summary", section), indent=2, ensure_ascii=False, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _render_html(report: dict[str, Any]) -> str:
    body = _render_md(report)
    safe = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>web2rag audit — {report['generated_at']}</title>
<style>
  body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, sans-serif; max-width: 960px; margin: 2em auto; padding: 0 1em; color: #111; }}
  pre {{ background: #f6f8fa; padding: 16px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }}
  h1, h2 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }}
</style>
</head>
<body>
<pre>{safe}</pre>
</body>
</html>
"""
