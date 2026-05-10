"""DeepEval correctness runner — Faithfulness, AnswerRelevancy,
ContextualPrecision, ContextualRecall.

We standardised on DeepEval for the quality axis (was RAGAS in an earlier
draft) so all three eval surfaces — correctness, capability, security —
share the same framework, the same judge factory, and the same
`LLMTestCase` shape. Less code, less surface area for version drift.
"""
from __future__ import annotations

import logging
from typing import Any

from eval.judges.factory import JudgeLLM
from eval.runners._adapters import DeepEvalAdapter

log = logging.getLogger(__name__)


async def run(
    *,
    judge: JudgeLLM,
    samples: list[dict[str, Any]],
    answer_for: callable,  # type: ignore[type-arg]  # async (question) -> {"answer": str, "contexts": [str]}
) -> dict[str, Any]:
    if not samples:
        return {"summary": {}, "per_sample": [], "judge": judge.spec.spec}

    try:
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            FaithfulnessMetric,
        )
        from deepeval.test_case import LLMTestCase
    except ImportError as e:
        raise RuntimeError("deepeval must be installed") from e

    from server.config import settings
    adapter = DeepEvalAdapter(judge)
    th = settings.eval_metric_threshold
    metrics = {
        "faithfulness":         FaithfulnessMetric(threshold=th, model=adapter, async_mode=False),
        "answer_relevancy":     AnswerRelevancyMetric(threshold=th, model=adapter, async_mode=False),
        "contextual_precision": ContextualPrecisionMetric(threshold=th, model=adapter, async_mode=False),
        "contextual_recall":    ContextualRecallMetric(threshold=th, model=adapter, async_mode=False),
    }

    per_sample: list[dict[str, Any]] = []
    aggregate: dict[str, list[float]] = {k: [] for k in metrics}

    for sample in samples:
        q = sample.get("question") or ""
        if not q:
            continue
        bot = await answer_for(q)
        case = LLMTestCase(
            input=q,
            actual_output=bot["answer"],
            expected_output=sample.get("ground_truth", "") or None,
            retrieval_context=bot["contexts"],
            context=sample.get("contexts", []),
        )
        scores: dict[str, dict[str, Any]] = {}
        for name, metric in metrics.items():
            try:
                metric.measure(case)
                score = float(metric.score or 0.0)
                scores[name] = {"score": score, "passed": bool(metric.is_successful())}
                aggregate[name].append(score)
            except Exception as e:  # noqa: BLE001
                scores[name] = {"score": None, "passed": None, "error": str(e)}
        per_sample.append({"question": q, "answer": bot["answer"], "scores": scores})

    summary = {
        name: {
            "mean_score": (sum(v) / len(v)) if v else None,
            "n": len(v),
            "pass_rate": (sum(1 for x in v if x >= th) / len(v)) if v else None,
        }
        for name, v in aggregate.items()
    }
    return {"summary": summary, "per_sample": per_sample, "judge": judge.spec.spec, "n_samples": len(per_sample)}
