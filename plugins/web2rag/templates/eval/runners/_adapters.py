"""Adapter that fits our JudgeLLM into deepeval's interface.

We use DeepEval for correctness + capability and DeepTeam for security.
Both consume the same `DeepEvalBaseLLM` shape, so one adapter covers
both runners.

DeepEval's `initialize_model` does an isinstance() check rather than
duck typing, so this class MUST subclass `DeepEvalBaseLLM`. The base
class is constructed lazily inside the factory (we wrap our own
JudgeLLM and ignore DeepEval's model_name plumbing) — see the
DeepEvalAdapter __init__ below.
"""
from __future__ import annotations

from eval.judges.factory import JudgeLLM


def _base_class() -> type:
    """Lazy import — pulling deepeval at module-load time slows every
    server startup with a heavy ML dep we only need during /audit."""
    from deepeval.models.base_model import DeepEvalBaseLLM  # type: ignore[import-untyped]
    return DeepEvalBaseLLM


_JSON_FENCE_RE = None  # lazily compiled


def _strip_json_fence(text: str) -> str:
    """Pull JSON out of a fenced markdown block if the model wrapped it.
    Idempotent for plain JSON."""
    global _JSON_FENCE_RE
    if _JSON_FENCE_RE is None:
        import re
        _JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)
    m = _JSON_FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _parse_with_schema(text: str, schema):  # type: ignore[no-untyped-def]
    """Best-effort: parse model output as JSON, then coerce into the
    pydantic schema. Falls back to {} (then schema validation may fail)
    if no JSON object is found."""
    import json
    cleaned = _strip_json_fence(text)
    # If the model wrapped its JSON in prose, find the first {...} block.
    if not cleaned.startswith("{") and not cleaned.startswith("["):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last-resort: try to give the schema something it might accept.
        data = {}
    # pydantic v2 uses model_validate; v1 uses parse_obj.
    if hasattr(schema, "model_validate"):
        return schema.model_validate(data)
    return schema.parse_obj(data)  # type: ignore[no-any-return]


def _augment_prompt_for_schema(prompt: str, schema) -> str:  # type: ignore[no-untyped-def]
    """Add an instruction to reply with JSON matching the schema.

    For local LLMs without native structured-output support, this is
    the cheapest path. For vLLM specifically you could use
    `response_format={"type": "json_schema", ...}` for stronger
    guarantees — left as an enhancement.
    """
    if hasattr(schema, "model_json_schema"):
        schema_str = schema.model_json_schema()
    elif hasattr(schema, "schema"):
        schema_str = schema.schema()
    else:
        schema_str = str(schema)
    import json as _json
    return (
        f"{prompt}\n\n"
        "Reply with ONLY valid JSON that matches this schema. No prose, "
        "no markdown fences, no commentary:\n"
        f"```json\n{_json.dumps(schema_str, indent=2)}\n```"
    )


def _build_adapter() -> type:
    """Build the adapter class with DeepEvalBaseLLM as its base. We do
    this in a function instead of at module top so importing this file
    doesn't drag deepeval into the api's import graph."""
    base = _base_class()

    class DeepEvalAdapter(base):  # type: ignore[misc, valid-type]
        """Wraps a JudgeLLM into something DeepEval / DeepTeam accepts.

        DeepEval's modern API can pass a `schema=PydanticModel` keyword
        to request structured output. Native model implementations use
        the provider's structured-output feature (Anthropic tool-use,
        OpenAI response_format, Ollama format=json, etc.). For our
        generic JudgeLLM (which only exposes raw text), we fall back to
        prompt-augmentation: tell the model to emit JSON matching the
        schema and parse + coerce the result. Works well with capable
        instruct-tuned models (Qwen2.5-14B, Llama-3.1-70B-Instruct, etc.);
        smaller models occasionally produce malformed JSON.
        """

        def __init__(self, judge: JudgeLLM) -> None:
            # Skip super().__init__ — DeepEvalBaseLLM's constructor
            # takes a model_name argument we don't have a clean value
            # for (our judge is described by JudgeSpec, not a string).
            self._judge = judge

        def load_model(self):  # type: ignore[no-untyped-def]
            return self

        def generate(self, prompt: str, schema=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            if schema is not None:
                augmented = _augment_prompt_for_schema(prompt, schema)
                text = self._judge.generate_sync(augmented)
                return _parse_with_schema(text, schema)
            return self._judge.generate_sync(prompt)

        async def a_generate(self, prompt: str, schema=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            if schema is not None:
                augmented = _augment_prompt_for_schema(prompt, schema)
                text = await self._judge.generate(augmented)
                return _parse_with_schema(text, schema)
            return await self._judge.generate(prompt)

        def get_model_name(self) -> str:
            return self._judge.spec.model

    return DeepEvalAdapter


# Cache the constructed class so repeated imports don't pay the deepeval
# import cost more than once.
_ADAPTER_CLASS: type | None = None


def DeepEvalAdapter(judge: JudgeLLM):  # type: ignore[no-untyped-def]
    """Public factory — returns an instance of the lazily-built adapter
    class. Call sites can stay as `DeepEvalAdapter(judge)` exactly like
    before; the class construction is hidden inside this wrapper."""
    global _ADAPTER_CLASS
    if _ADAPTER_CLASS is None:
        _ADAPTER_CLASS = _build_adapter()
    return _ADAPTER_CLASS(judge)
