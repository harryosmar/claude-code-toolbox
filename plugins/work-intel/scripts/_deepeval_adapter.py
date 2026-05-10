"""DeepEvalBaseLLM adapter that lets deepteam guards reuse the shared judge.

Bridges scripts/_llm_judge.py (LangChain ChatAnthropic / ChatOllama) to the
DeepEvalBaseLLM interface that deepteam.guardrails.Guardrails accepts.

Used ONLY in the Tier C batch path (security_audit.py). Inline guards
(Tier A query/output via Claude Code subagent, Tier B ingest via Presidio)
do not import this.

Lazy-imports deepeval so the rest of work-intel keeps working when only
RAGAS is installed.
"""
from __future__ import annotations

import os
import sys
from typing import Any

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _make_adapter_class():
    """Build the adapter class lazily (deepeval import deferred)."""
    try:
        from deepeval.models.base_model import DeepEvalBaseLLM
    except ImportError as e:
        raise ImportError(
            "deepeval / deepteam not installed. "
            "Run: pip install deepteam (pulls deepeval as a dependency)"
        ) from e

    class WorkIntelJudge(DeepEvalBaseLLM):
        """Wraps the work-intel shared LangChain judge for deepteam guards."""

        def __init__(self, cfg: dict[str, Any]):
            from _llm_judge import get_judge  # type: ignore[import-not-found]
            self._llm, self._label = get_judge(cfg)

        def load_model(self):  # type: ignore[override]
            return self._llm

        def generate(self, prompt: str) -> str:  # type: ignore[override]
            response = self._llm.invoke(prompt)
            return _extract_text(response)

        async def a_generate(self, prompt: str) -> str:  # type: ignore[override]
            response = await self._llm.ainvoke(prompt)
            return _extract_text(response)

        def get_model_name(self) -> str:  # type: ignore[override]
            return self._label

    return WorkIntelJudge


def _extract_text(response: Any) -> str:
    """LangChain returns AIMessage / BaseMessage with `.content` (str or list)."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Anthropic streaming style returns a list of content blocks.
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
        return "".join(parts)
    return str(content)


def make_judge(cfg: dict[str, Any]):
    """Public factory — call from security_audit.py.

    Returns an instance of the adapter class. Raises ImportError if deepeval
    isn't installed (caller should print a setup hint).
    """
    adapter_class = _make_adapter_class()
    return adapter_class(cfg)
