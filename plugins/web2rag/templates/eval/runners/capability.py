"""DeepEval capability runner — GEval over helpfulness + task completion.

This is the auto-eval proxy for the four-axis model's CAPABILITY axis.
Real signal still comes from production thumbs-up/down (server/api/feedback.py),
but GEval gives us a continuous CI-friendly score over the testset.
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
    answer_for: callable,  # type: ignore[type-arg]
) -> dict[str, Any]:
    if not samples:
        return {"summary": {}, "per_sample": [], "judge": judge.spec.spec}

    try:
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
    except ImportError as e:
        raise RuntimeError("deepeval must be installed") from e

    from server.config import settings
    adapter = DeepEvalAdapter(judge)
    th = settings.eval_geval_threshold
    metrics = {
        "helpfulness": GEval(
            name="Helpfulness",
            criteria=(
                "Does the response give the user enough information to act on, "
                "rather than vaguely acknowledging the question or deflecting? "
                "Score lower for hedging, higher for concrete actionable answers."
            ),
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
            model=adapter,
            threshold=th,
            async_mode=False,
        ),
        "task_completion": GEval(
            name="TaskCompletion",
            criteria=(
                "Did the response complete the task in the user's input? "
                "Score 1.0 for fully addressed, ~0.5 for partial, near 0 for refused or off-topic."
            ),
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
            model=adapter,
            threshold=th,
            async_mode=False,
        ),
        "scope_adherence": GEval(
            name="ScopeAdherence",
            criteria=(
                "Did the response stay within the scope of the provided context, "
                "and refuse politely when the answer was not in the docs?"
            ),
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.RETRIEVAL_CONTEXT,
            ],
            model=adapter,
            threshold=th,
            async_mode=False,
        ),
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
            retrieval_context=bot["contexts"],
        )
        scores: dict[str, dict[str, Any]] = {}
        for name, metric in metrics.items():
            try:
                metric.measure(case)
                score = float(metric.score or 0.0)
                scores[name] = {"score": score, "passed": bool(metric.is_successful()), "reason": getattr(metric, "reason", "") or ""}
                aggregate[name].append(score)
            except Exception as e:  # noqa: BLE001
                scores[name] = {"score": None, "passed": None, "error": str(e)}
        per_sample.append({"question": q, "answer": bot["answer"], "scores": scores})

    summary = {
        name: {"mean_score": (sum(v) / len(v)) if v else None, "n": len(v)}
        for name, v in aggregate.items()
    }
    return {"summary": summary, "per_sample": per_sample, "judge": judge.spec.spec, "n_samples": len(per_sample)}
