"""Settings singleton — every tunable in the api lives here.

Anything else in server/ or eval/ that needs configuration imports
`settings` from this module, never reads os.environ directly. That keeps
the env contract auditable in one file: any operator who wants to know
"what knobs does this api expose?" reads server/config.py once.

Hardcoded magic numbers in other modules are an anti-pattern — they
hide the contract. If you find yourself writing `max_tokens=1024` or
`threshold=0.7` somewhere, move it here and reference settings.X.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ChatModel = Literal["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
PromptGuardBackend = Literal["local", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ─── chat LLM ────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    chat_model: ChatModel = Field(default="claude-haiku-4-5-20251001", validation_alias="CHAT_MODEL")
    chat_max_tokens: int = Field(default=1024, validation_alias="CHAT_MAX_TOKENS")
    rewrite_max_tokens: int = Field(default=256, validation_alias="REWRITE_MAX_TOKENS")

    # ─── eval judge ──────────────────────────────────────────────────────────
    ollama_url: str = Field(default="", validation_alias="OLLAMA_URL")
    ollama_timeout_s: int = Field(default=120, validation_alias="OLLAMA_TIMEOUT_S")
    judge_max_tokens: int = Field(default=1024, validation_alias="JUDGE_MAX_TOKENS")
    # Cap on in-flight requests to a self-hosted judge (custom-openai /
    # openai-compat). DeepEval and DeepTeam fan out via asyncio.gather and
    # will happily fire 100+ concurrent POSTs at the judge server. If that
    # exceeds the server's `--max-num-seqs`, requests queue and every call
    # pays queue-wait latency on top of generation. Matching this knob to
    # the server's max-num-seqs (typical small-vLLM default: 8) gives the
    # cleanest behavior — same wallclock as today, no queue thrash, no
    # timeout cascades. Has no effect on the Anthropic judge (its own
    # client manages backpressure against Anthropic's ratelimits).
    judge_max_concurrency: int = Field(default=8, validation_alias="JUDGE_MAX_CONCURRENCY")
    eval_metric_threshold: float = Field(default=0.7, validation_alias="EVAL_METRIC_THRESHOLD")
    eval_geval_threshold: float = Field(default=0.6, validation_alias="EVAL_GEVAL_THRESHOLD")
    audit_use_batch_api: bool = Field(default=False, validation_alias="AUDIT_USE_BATCH_API")
    batch_poll_interval_s: int = Field(default=15, validation_alias="BATCH_POLL_INTERVAL_S")
    batch_max_wait_s: int = Field(default=24 * 3600, validation_alias="BATCH_MAX_WAIT_S")

    # ─── guards ──────────────────────────────────────────────────────────────
    faithfulness_threshold: float = Field(default=0.55, validation_alias="FAITHFULNESS_THRESHOLD")
    # Prompt-guard pluggable backend.
    #   "local"     — in-process Hugging Face transformers model
    #                 (uses PROMPT_GUARD_MODEL). $0 per check, ~50–100ms,
    #                 robust against prompt-inject-the-judge.
    #   "anthropic" — Claude as classifier via tool-use (uses
    #                 PROMPT_GUARD_LLM_MODEL). ~$0.001/check on Haiku,
    #                 ~300–600ms, multilingual natively. No HF auth.
    prompt_guard_backend: PromptGuardBackend = Field(
        default="local",
        validation_alias="PROMPT_GUARD_BACKEND",
    )
    # Local-backend model. Default is meta-llama/Llama-Prompt-Guard-2-86M
    # (multilingual, ~85 MB, 3-class BENIGN/INJECTION/JAILBREAK). HF-gated
    # — accept the Meta licence on the model page and set HF_TOKEN below.
    # No-auth fallback: protectai/deberta-v3-base-prompt-injection-v2
    # (English-only, 2-class).
    prompt_guard_model: str = Field(
        default="meta-llama/Llama-Prompt-Guard-2-86M",
        validation_alias="PROMPT_GUARD_MODEL",
    )
    # Anthropic-backend model. Haiku is the cost/quality sweet spot for
    # binary safety classification — Sonnet is wasteful here. Both are
    # multilingual.
    prompt_guard_llm_model: str = Field(
        default="claude-haiku-4-5-20251001",
        validation_alias="PROMPT_GUARD_LLM_MODEL",
    )
    # LRU cache size for the Anthropic backend. Keyed by sha256(model||text)
    # so model swaps don't poison the cache. Tune up for ingest-heavy
    # workloads (each page is a unique cache miss); tune down for memory.
    prompt_guard_llm_cache_size: int = Field(
        default=4096,
        validation_alias="PROMPT_GUARD_LLM_CACHE_SIZE",
    )
    # Hugging Face token used by transformers.from_pretrained for gated
    # models (only relevant when prompt_guard_backend=local with a gated
    # PROMPT_GUARD_MODEL). Read at startup; if missing and the configured
    # model is gated, the api refuses to start with an actionable error.
    hf_token: str = Field(default="", validation_alias="HF_TOKEN")
    prompt_injection_high_threshold: float = Field(default=0.9, validation_alias="PROMPT_INJECTION_HIGH_THRESHOLD")
    prompt_injection_med_threshold: float = Field(default=0.5, validation_alias="PROMPT_INJECTION_MED_THRESHOLD")
    prompt_injection_window_chars: int = Field(default=2000, validation_alias="PROMPT_INJECTION_WINDOW_CHARS")
    prompt_injection_window_stride: int = Field(default=1500, validation_alias="PROMPT_INJECTION_WINDOW_STRIDE")
    prompt_injection_max_seq_len: int = Field(default=512, validation_alias="PROMPT_INJECTION_MAX_SEQ_LEN")
    # Languages on which the prompt-injection detector is trusted. When a
    # detected language is NOT in this set the guard skips (logged as
    # `prompt_injection_skipped:lang=…`). With the default Prompt-Guard-2
    # model (multilingual) the safe default is "id,en" — both your primary
    # and secondary languages are covered. If you swap to the EN-only
    # protectai/deberta model, narrow this to "en" or accept false positives
    # on other languages.
    prompt_injection_languages: str = Field(default="id,en", validation_alias="PROMPT_INJECTION_LANGUAGES")
    toxicity_threshold: float = Field(default=0.7, validation_alias="TOXICITY_THRESHOLD")
    toxicity_max_seq_len: int = Field(default=512, validation_alias="TOXICITY_MAX_SEQ_LEN")
    topic_scope_threshold: float = Field(default=0.20, validation_alias="TOPIC_SCOPE_THRESHOLD")
    topic_scope_cache_ttl_s: float = Field(default=600.0, validation_alias="TOPIC_SCOPE_CACHE_TTL_S")

    # ─── presidio (PII redaction) — bilingual EN+ID tuning ──────────────────
    # Master kill-switch. Set true on sites where the operator has audited
    # the corpus and confirmed no PII (or where downstream scrubbing handles
    # PII separately). Skips Presidio entirely.
    presidio_disabled: bool = Field(default=False, validation_alias="PRESIDIO_DISABLED")
    # Entity types to detect for English content. Empty (default) means
    # "use Presidio's full default recognizer set" — PERSON / LOCATION /
    # ORGANIZATION via spaCy NER plus all regex/checksum recognizers.
    presidio_en_entities: str = Field(default="", validation_alias="PRESIDIO_EN_ENTITIES")
    # Entity types to detect for Indonesian content. Default is regex-only —
    # spaCy's English NER false-positives heavily on Indonesian (every news
    # article becomes <PERSON>/<LOCATION>/<ORGANIZATION> soup). The default
    # set keeps the language-agnostic recognizers (email, phone, credit
    # card, IBAN, IP, URL, crypto, US SSN) plus the custom Indonesian PII
    # recognizers we register (NIK 16-digit, NPWP). Override with a
    # non-empty list to customize, or set to "*" to enable PERSON/LOC/ORG
    # too (not recommended unless you've registered an Indonesian spaCy
    # model).
    presidio_id_entities: str = Field(
        default=(
            "EMAIL_ADDRESS,PHONE_NUMBER,CREDIT_CARD,IBAN_CODE,"
            "IP_ADDRESS,URL,CRYPTO,US_SSN,ID_NIK,ID_NPWP"
        ),
        validation_alias="PRESIDIO_ID_ENTITIES",
    )
    # Toggle Indonesian PII pattern recognizers (NIK 16-digit, NPWP). Set
    # false to skip registering them at startup if your corpus is purely
    # English. Has no effect on detection if PRESIDIO_ID_ENTITIES doesn't
    # include ID_NIK / ID_NPWP.
    presidio_id_patterns_enabled: bool = Field(
        default=True, validation_alias="PRESIDIO_ID_PATTERNS_ENABLED"
    )

    # ─── crawl politeness ────────────────────────────────────────────────────
    respect_robots_txt: bool = Field(default=True, validation_alias="RESPECT_ROBOTS_TXT")
    crawl_rate_limit: str = Field(default="10/60s", validation_alias="CRAWL_RATE_LIMIT")
    crawl_concurrency: int = Field(default=2, validation_alias="CRAWL_CONCURRENCY")
    crawl_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        ),
        validation_alias="CRAWL_USER_AGENT",
    )
    crawl_request_timeout_s: int = Field(default=30, validation_alias="CRAWL_REQUEST_TIMEOUT_S")
    crawl_max_page_bytes: int = Field(default=2 * 1024 * 1024, validation_alias="CRAWL_MAX_PAGE_BYTES")
    crawl_default_max_pages: int = Field(default=100, validation_alias="CRAWL_DEFAULT_MAX_PAGES")
    crawl_default_max_depth: int = Field(default=2, validation_alias="CRAWL_DEFAULT_MAX_DEPTH")

    # ─── chunker ─────────────────────────────────────────────────────────────
    chunk_size_chars: int = Field(default=2048, validation_alias="CHUNK_SIZE_CHARS")
    chunk_overlap_chars: int = Field(default=320, validation_alias="CHUNK_OVERLAP_CHARS")

    # ─── retrieval ───────────────────────────────────────────────────────────
    # Context compression: hybrid retrieves top-K, reranker keeps top-N for the LLM.
    # Lower top-N = fewer input tokens to the chat LLM = lower per-call cost.
    # top_k controls how many candidates the reranker scores; this is the
    # dominant cost on CPU (each candidate is one cross-encoder forward pass).
    # 10 is the cost/quality sweet spot for most corpora — bump to 20 for
    # very dense docs sites where the dense+BM25 fusion misses good chunks
    # in the top 10. /retrieve accepts a per-request override for ad-hoc tuning.
    retrieve_top_k: int = Field(default=10, validation_alias="RETRIEVE_TOP_K")
    rerank_top_n: int = Field(default=3, validation_alias="RERANK_TOP_N")
    # Cross-encoder forward-pass batch size. With top_k=10 all candidates fit
    # in one batch (10 < default 32) so the knob is mostly forward-looking —
    # if an operator bumps RETRIEVE_TOP_K=100, this caps memory usage.
    # Lower this to 16 on memory-constrained boxes; raise to 64 on machines
    # with VRAM/RAM headroom for marginal CPU-cache wins.
    rerank_batch_size: int = Field(default=32, validation_alias="RERANK_BATCH_SIZE")
    rrf_k: int = Field(default=60, validation_alias="RRF_K")
    # Auto-acronym query expansion (B1). When the corpus's self-extracted
    # acronym table contains a term in the user's query, hybrid_search
    # generates expanded variants and RRF-fuses across them. This caps the
    # variant count to bound retrieval fan-out: each extra variant adds one
    # embedder pass + one chroma round-trip + one BM25 pass. 3 is the
    # cost/quality sweet spot — enough to handle queries with 1-2 acronyms,
    # bounded enough that latency stays predictable. Set to 1 to disable
    # query expansion entirely (retrieval falls back to legacy single-query
    # behaviour without code changes).
    max_query_variants: int = Field(default=3, validation_alias="MAX_QUERY_VARIANTS")

    # HyDE (Hypothetical Document Embeddings). When enabled, /retrieve asks
    # an LLM to write a hypothetical answer to the user's question, then
    # embeds the hypothetical alongside the original query + acronym variants
    # and RRF-fuses all results. This bridges vocabulary gaps the auto-acronym
    # table can't cover (synonyms, paraphrases, definitional intent on a
    # corpus that uses formal phrasing).
    #
    # /chat (production) and /audit do NOT use HyDE — production stays on the
    # Claude path with native Citations API; audit must stay deterministic so
    # eval scores are reproducible across runs.
    #
    # Cost depends on HYDE_URL: pointing at a local vLLM/Ollama is $0;
    # pointing at Anthropic adds ~$0.0005/query on Haiku. Latency adds
    # 200ms-3s per /retrieve depending on the chosen LLM.
    hyde_enabled: bool = Field(default=False, validation_alias="HYDE_ENABLED")
    # OpenAI-compatible endpoint base URL (must end in /v1). Examples:
    #   http://192.0.2.5:8001/v1            (your local Qwen vLLM)
    #   https://api.anthropic.com/v1             (Anthropic — needs key)
    #   http://ollama-host:11434/v1              (Ollama with OpenAI shim)
    hyde_url: str = Field(default="", validation_alias="HYDE_URL")
    hyde_model: str = Field(default="", validation_alias="HYDE_MODEL")
    # Bearer token for paid providers. Leave empty for self-hosted vLLM/Ollama.
    hyde_api_key: str = Field(default="", validation_alias="HYDE_API_KEY")
    # Hypothetical answers should be short — 200 tokens is enough for a
    # 2-3 sentence definitional answer that captures corpus vocabulary.
    # Larger budgets waste latency without retrieval benefit.
    hyde_max_tokens: int = Field(default=200, validation_alias="HYDE_MAX_TOKENS")
    # Hard cap on how long /retrieve waits for the HyDE LLM. On timeout the
    # call is dropped and retrieval proceeds without a hypothetical (graceful
    # degradation — partial signal is better than a 504 to the caller).
    hyde_timeout_s: int = Field(default=30, validation_alias="HYDE_TIMEOUT_S")

    # ─── eval testset ────────────────────────────────────────────────────────
    testset_default_size: int = Field(default=50, validation_alias="TESTSET_DEFAULT_SIZE")
    testset_context_window_chunks: int = Field(default=3, validation_alias="TESTSET_CONTEXT_WINDOW_CHUNKS")

    # ─── chroma ──────────────────────────────────────────────────────────────
    chroma_host: str = Field(default="localhost", validation_alias="CHROMA_HOST")
    chroma_port: int = Field(default=8000, validation_alias="CHROMA_PORT")

    # ─── server ──────────────────────────────────────────────────────────────
    api_port: int = Field(default=8787, validation_alias="API_PORT")
    log_level: str = Field(default="info", validation_alias="LOG_LEVEL")

    @property
    def prompt_injection_lang_set(self) -> set[str]:
        return {x.strip() for x in self.prompt_injection_languages.split(",") if x.strip()}

    @property
    def presidio_en_entity_set(self) -> list[str]:
        """Parsed PRESIDIO_EN_ENTITIES — empty list means 'use full default
        recognizer set' (caller passes None to Presidio analyser)."""
        return [x.strip() for x in self.presidio_en_entities.split(",") if x.strip()]

    @property
    def presidio_id_entity_set(self) -> list[str]:
        """Parsed PRESIDIO_ID_ENTITIES — empty list means 'use full default
        recognizer set'. The default value of PRESIDIO_ID_ENTITIES is the
        regex-only recognizer set, so this property is rarely empty unless
        the operator explicitly cleared it."""
        return [x.strip() for x in self.presidio_id_entities.split(",") if x.strip()]

    @field_validator("crawl_rate_limit")
    @classmethod
    def _rate_limit_format(cls, v: str) -> str:
        if not re.fullmatch(r"\d+/\d+[smh]", v):
            raise ValueError(
                f"CRAWL_RATE_LIMIT must look like '10/60s', '5/10s', '100/1h'; got {v!r}"
            )
        return v


@lru_cache(maxsize=1)
def _load() -> Settings:
    return Settings()


settings: Settings = _load()
