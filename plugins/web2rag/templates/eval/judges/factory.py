"""Eval judge factory — switchable per audit run.

Four specs:
  - "anthropic-haiku"               → Claude Haiku 4.5 via the Anthropic SDK
                                      (paid). Best quality at low cost.
  - "ollama:<model>"                → POST Ollama's native API at OLLAMA_URL.
                                      Free if the operator owns the GPU host.
                                      Endpoint: POST /api/generate.
  - "openai-compat:<model>"         → POST OpenAI-compatible /v1/chat/completions
                                      at OLLAMA_URL. Works with vLLM, LM Studio,
                                      llama.cpp's server, Ollama-with-OpenAI-shim,
                                      and any other server that implements the
                                      OpenAI Chat Completions schema.
                                      <model> is whatever id the server lists
                                      under GET /v1/models (e.g. "Qwen/Qwen2.5-
                                      14B-Instruct-AWQ" on vLLM).
  - "custom-openai:<url>:<model>"   → Same wire protocol as openai-compat, but
                                      the base URL is passed inline in the spec
                                      instead of read from OLLAMA_URL. Use this
                                      when you want a single self-contained
                                      --judge flag (e.g. ad-hoc audits against
                                      a teammate's box, or CI where OLLAMA_URL
                                      is reserved for a different server).
                                      <url> must include `/v1`, e.g.
                                      `http://192.0.2.5:8001/v1`.
                                      <model> must not contain `:` — if it
                                      does, fall back to openai-compat:<model>
                                      with OLLAMA_URL set.

The chat path is unaffected: changing the judge for one /audit call has no
effect on /chat — that's wired straight to settings.chat_model in
server/llm/claude.py.

The returned JudgeLLM exposes a single `generate(prompt, system=...)` method.
The runners in eval/runners/ wrap it however deepeval / deepteam need —
see runners/_adapters.py.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from server.config import settings

log = logging.getLogger(__name__)

_DEFAULT_JUDGE_SYSTEM = "You are a careful evaluation judge. Be precise and concise."


@dataclass(frozen=True)
class JudgeSpec:
    """How the judge identifies itself in audit reports."""
    spec: str           # the original "anthropic-haiku" or "ollama:llama3.1"
    provider: str       # "anthropic" | "ollama"
    model: str          # underlying model name


class JudgeLLM(ABC):
    """Single async generate() interface. Adapters in runners/_adapters.py
    wrap this for deepeval (DeepEvalBaseLLM) and ragas (BaseRagasLLM)."""

    spec: JudgeSpec

    @abstractmethod
    async def generate(self, prompt: str, *, system: str = "") -> str:
        ...

    def generate_sync(self, prompt: str, *, system: str = "") -> str:
        """Convenience for frameworks that need sync access. Runs the async
        path in a fresh event loop or schedules onto the running one."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.generate(prompt, system=system))
        # Already inside a loop — let the caller await; we shouldn't block.
        future = asyncio.run_coroutine_threadsafe(self.generate(prompt, system=system), loop)
        return future.result()


class _AnthropicHaikuJudge(JudgeLLM):
    spec = JudgeSpec(spec="anthropic-haiku", provider="anthropic", model="claude-haiku-4-5-20251001")

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for --judge anthropic-haiku")
        from anthropic import AsyncAnthropic
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._batch = settings.audit_use_batch_api

    async def generate(self, prompt: str, *, system: str = "") -> str:
        # The realtime path. Used by chat-flow callers and by the audit
        # runners when AUDIT_USE_BATCH_API=false. The batch-mode path lives
        # in `generate_batch()` below — DeepEval's measure() loop is
        # synchronous and call-by-call, so per-prompt batching there would
        # require deepeval-internal changes. We expose generate_batch() so
        # places we control (like testset generation) can opt in.
        resp = await self._client.messages.create(
            model=self.spec.model,
            max_tokens=settings.judge_max_tokens,
            system=system or _DEFAULT_JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text").strip()

    async def generate_batch(
        self,
        prompts: list[str],
        *,
        system: str = "",
        custom_id_prefix: str = "audit",
    ) -> list[str]:
        """Submit N prompts as a single Anthropic Batch API job.

        50% cheaper than realtime; completes within 24h (usually minutes).
        Use when the audit pipeline can tolerate latency — testset
        generation, daily-digest summaries, etc. Chat must NOT call this.

        Returns results in the same order as `prompts`.
        """
        from anthropic.types.messages.batch_create_params import Request
        # The async client exposes batches under .messages.batches.
        # Each Request wraps a normal MessagesCreateParams payload.
        sys_text = system or _DEFAULT_JUDGE_SYSTEM
        requests = [
            Request(
                custom_id=f"{custom_id_prefix}-{i}",
                params={
                    "model": self.spec.model,
                    "max_tokens": settings.judge_max_tokens,
                    "system": sys_text,
                    "messages": [{"role": "user", "content": p}],
                },
            )
            for i, p in enumerate(prompts)
        ]
        batch = await self._client.messages.batches.create(requests=requests)

        # Poll until the batch finishes. Most batches finish in minutes;
        # batch_max_wait_s caps at the SLA (default 24h).
        import asyncio
        deadline = asyncio.get_event_loop().time() + settings.batch_max_wait_s
        while True:
            current = await self._client.messages.batches.retrieve(batch.id)
            if current.processing_status == "ended":
                break
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"batch {batch.id} did not finish within {settings.batch_max_wait_s}s")
            await asyncio.sleep(settings.batch_poll_interval_s)

        # Stream results back. The order is by custom_id; we restore the
        # original ordering before returning.
        out_by_idx: dict[int, str] = {}
        async for entry in self._client.messages.batches.results(batch.id):
            cid = entry.custom_id  # type: ignore[union-attr]
            idx = int(cid.rsplit("-", 1)[1])
            if entry.result.type == "succeeded":  # type: ignore[union-attr]
                msg = entry.result.message  # type: ignore[union-attr]
                out_by_idx[idx] = "".join(b.text for b in msg.content if b.type == "text").strip()
            else:
                out_by_idx[idx] = ""
        return [out_by_idx.get(i, "") for i in range(len(prompts))]


class _OllamaJudge(JudgeLLM):
    def __init__(self, model: str) -> None:
        if not settings.ollama_url:
            raise RuntimeError("OLLAMA_URL is required for --judge ollama:<model>")
        self.spec = JudgeSpec(spec=f"ollama:{model}", provider="ollama", model=model)
        import httpx
        self._client = httpx.AsyncClient(timeout=settings.ollama_timeout_s, base_url=settings.ollama_url.rstrip("/"))

    async def generate(self, prompt: str, *, system: str = "") -> str:
        body: dict[str, Any] = {
            "model": self.spec.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            body["system"] = system
        r = await self._client.post("/api/generate", json=body)
        r.raise_for_status()
        return (r.json().get("response") or "").strip()


class _OpenAICompatJudge(JudgeLLM):
    """Generic OpenAI-compatible Chat Completions judge.

    Works against any server that implements POST /v1/chat/completions —
    vLLM, LM Studio, llama.cpp's server, Ollama with the OpenAI shim, or
    any other gateway. Reads the base URL from OLLAMA_URL (kept as a
    generic 'remote LLM URL' for backward compatibility — rename to
    EVAL_JUDGE_URL in a future major if it becomes confusing).

    The base URL must already include `/v1`, e.g.:
        OLLAMA_URL=http://192.0.2.5:8001/v1
    The class appends `/chat/completions` to that.
    """

    def __init__(self, model: str) -> None:
        if not settings.ollama_url:
            raise RuntimeError(
                "OLLAMA_URL is required for --judge openai-compat:<model> "
                "(it's used as the generic remote LLM base URL — set it to "
                "your server's /v1 path, e.g. http://host:8001/v1)."
            )
        self.spec = JudgeSpec(spec=f"openai-compat:{model}", provider="openai-compat", model=model)
        import httpx
        self._client = httpx.AsyncClient(
            timeout=settings.ollama_timeout_s,
            base_url=settings.ollama_url.rstrip("/"),
        )

    async def generate(self, prompt: str, *, system: str = "") -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            "max_tokens": settings.judge_max_tokens,
            "temperature": 0.0,  # deterministic — judges should be reproducible
            "stream": False,
        }
        r = await self._client.post("/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        # Standard OpenAI shape: choices[0].message.content
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return (msg.get("content") or "").strip()


class _CustomOpenAIJudge(JudgeLLM):
    """OpenAI-compatible judge with the base URL passed inline.

    Same wire protocol as `_OpenAICompatJudge` (POST /chat/completions with
    the standard OpenAI schema) but the URL comes from the audit-run spec
    instead of an env var. This makes `--judge custom-openai:<url>:<model>`
    fully self-contained — no .env edit, no operator coordination — which
    is the right ergonomic when you're testing against a sibling team's
    vLLM box or running ad-hoc evals from CI where OLLAMA_URL is taken.

    Required URL shape: must include `/v1` (we append `/chat/completions`).
        http://192.0.2.5:8001/v1
        https://judge.internal.acme.com/v1
    """

    def __init__(self, url: str, model: str) -> None:
        if not url:
            raise ValueError("custom-openai:<url>:<model> requires a non-empty url")
        if not model:
            raise ValueError("custom-openai:<url>:<model> requires a non-empty model id")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(
                f"custom-openai url must start with http:// or https:// (got {url!r})"
            )
        self.spec = JudgeSpec(spec=f"custom-openai:{url}:{model}", provider="custom-openai", model=model)
        self._url = url
        import asyncio
        import httpx
        self._client = httpx.AsyncClient(
            timeout=settings.ollama_timeout_s,
            base_url=url.rstrip("/"),
        )
        # In-flight cap. DeepEval/DeepTeam fan out via asyncio.gather and
        # will oversubscribe a small vLLM box. Without the semaphore, an
        # 8-slot server hit with 200 concurrent POSTs queues 192 of them
        # behind the first 8, every call pays queue-wait, and timeouts
        # cascade. With the semaphore matched to the server's max-num-seqs,
        # only 8 are in flight at once — same wallclock, no queue thrash.
        # See server/config.py:judge_max_concurrency for the contract.
        self._sem = asyncio.Semaphore(max(1, settings.judge_max_concurrency))

    async def generate(self, prompt: str, *, system: str = "") -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.spec.model,
            "messages": messages,
            "max_tokens": settings.judge_max_tokens,
            "temperature": 0.0,
            "stream": False,
        }
        async with self._sem:
            r = await self._client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return (msg.get("content") or "").strip()


def _parse_custom_openai(rest: str) -> tuple[str, str]:
    """Split 'custom-openai:<url>:<model>' tail into (url, model).

    The URL contains its own colons (after the scheme, and the host:port
    pair), so naive `split(":", 1)` would eat them. We anchor on the last
    `:` that appears AFTER the URL path component starts — i.e. after the
    first `/` that follows `://`. Anything before that index is URL,
    anything after is the model id.

    This means model ids must not contain `:` (Ollama-style `qwen2.5:14b`
    tags would break). The factory's error message points at the
    openai-compat:<model> + OLLAMA_URL fallback for that case.
    """
    scheme_end = rest.find("://")
    if scheme_end < 0:
        raise ValueError(
            f"custom-openai url must start with http:// or https:// (got {rest!r})"
        )
    path_start = rest.find("/", scheme_end + 3)
    if path_start < 0:
        raise ValueError(
            "custom-openai url must include a path with /v1, "
            f"e.g. http://host:port/v1 (got {rest!r})"
        )
    sep = rest.rfind(":", path_start)
    if sep < 0:
        raise ValueError(
            "custom-openai spec must be 'custom-openai:<url>:<model>', "
            f"missing ':<model>' suffix (got {rest!r})"
        )
    url = rest[:sep]
    model = rest[sep + 1 :]
    return url, model


def create_judge(spec: str) -> JudgeLLM:
    if spec == "anthropic-haiku":
        return _AnthropicHaikuJudge()
    if spec.startswith("ollama:"):
        model = spec.split(":", 1)[1]
        if not model:
            raise ValueError("ollama:<model> requires a model name (e.g. ollama:llama3.1)")
        return _OllamaJudge(model)
    if spec.startswith("openai-compat:"):
        model = spec.split(":", 1)[1]
        if not model:
            raise ValueError(
                "openai-compat:<model> requires a model id (e.g. "
                "openai-compat:Qwen/Qwen2.5-14B-Instruct-AWQ)"
            )
        return _OpenAICompatJudge(model)
    if spec.startswith("custom-openai:"):
        url, model = _parse_custom_openai(spec[len("custom-openai:") :])
        return _CustomOpenAIJudge(url, model)
    raise ValueError(
        f"unknown judge spec: {spec!r}. Use 'anthropic-haiku', "
        "'ollama:<model>', 'openai-compat:<model>', or "
        "'custom-openai:<url>:<model>'."
    )
