"""DeepTeam red-team runner.

Why this is the security path (and DeepEval is not): DeepTeam owns the
red-team / adversarial side cleanly, while DeepEval's per-metric scorers
(PIILeakage, Toxicity, Bias, Hallucination) duplicate what the live
guard_query / guard_output already enforce on every chat call. We only
ship one library here — RAGAS for quality, DeepTeam for security.

DeepTeam internally drives a judge LLM and an attack simulator. We pass
the same JudgeLLM through the DeepEvalAdapter so a single `--judge` flag
controls both quality and red-team runs.
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
    answer_for: callable,  # type: ignore[type-arg]  — async (question) -> {"answer": str, "contexts": [...]}
) -> dict[str, Any]:
    """Run DeepTeam red-team simulators against the bot's chat path.

    answer_for(question) is the chat bridge: it returns whatever the bot
    would have replied. The DeepTeam model_callback wraps it with the
    RTTurn shape DeepTeam expects.
    """
    # Lazy imports — heavy deps only when audit fires.
    try:
        from deepteam.attacks.single_turn import PromptInjection, ROT13
        from deepteam.red_teamer import RedTeamer
        from deepteam.vulnerabilities import Bias, PromptLeakage, Toxicity
    except ImportError as e:
        raise RuntimeError("deepteam must be installed for the redteam runner") from e

    # Multi-turn attacks (e.g. LinearJailbreaking) intentionally NOT included
    # here. DeepTeam's multi-turn implementations re-initialize their
    # simulator model inside attack.__init__'s downstream calls and fall
    # back to OpenAI's GPT family when they can't find a string-named
    # model — making them incompatible with custom DeepEvalBaseLLM
    # adapters unless OPENAI_API_KEY is also set. Single-turn attacks
    # honour the RedTeamer's simulator_model parameter cleanly.

    adapter = DeepEvalAdapter(judge)

    async def model_callback(input: str, turns: list | None = None) -> str:  # noqa: A002, ARG001
        # DeepTeam's evaluation metrics concatenate the callback return
        # value into a string template, so it must be a plain str (not
        # an RTTurn or other wrapper). Earlier DeepTeam versions accepted
        # RTTurn here; the current version does not.
        result = await answer_for(input)
        return result["answer"]

    # RedTeamer is stateful and accepts the judge for both attack simulation
    # and verdict scoring. If your DeepTeam version moves the parameter
    # names, the adapter still works — DeepEvalAdapter conforms to
    # deepeval.models.base_model.DeepEvalBaseLLM.
    teamer = RedTeamer(
        simulator_model=adapter,    # type: ignore[arg-type]
        evaluation_model=adapter,   # type: ignore[arg-type]
        async_mode=True,
    )

    assessment = teamer.red_team(
        model_callback=model_callback,
        vulnerabilities=[
            Bias(types=["race", "gender"]),
            Toxicity(),
            PromptLeakage(),
        ],
        attacks=[
            PromptInjection(weight=2),
            ROT13(weight=1),
        ],
        attacks_per_vulnerability_type=2,
    )

    return {
        "summary": _summarise(assessment),
        "judge": judge.spec.spec,
    }


def _summarise(assessment: Any) -> dict[str, Any]:
    """Pull whatever risk fields the DeepTeam assessment object exposes.

    DeepTeam's API surface evolves; we deliberately read attributes
    defensively so a minor version bump doesn't break the audit report.
    """
    out: dict[str, Any] = {}
    for attr in ("overall", "overall_risk", "score", "vulnerability_scores"):
        if hasattr(assessment, attr):
            try:
                out[attr] = getattr(assessment, attr)
            except Exception:  # noqa: BLE001
                pass
    # Some versions return a Pydantic model — capture its dict form.
    if hasattr(assessment, "model_dump"):
        try:
            out["full"] = assessment.model_dump()
        except Exception:  # noqa: BLE001
            pass
    elif hasattr(assessment, "dict"):
        try:
            out["full"] = assessment.dict()
        except Exception:  # noqa: BLE001
            pass
    return out
