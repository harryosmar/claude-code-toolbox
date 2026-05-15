"""Concrete LLM adapters implementing the ports in ``server.llm.ports``.

  - **anthropic_adapter.py**  — AnthropicChatAdapter + AnthropicRewriteAdapter.
    Full feature parity (native Citations API, DocumentBlockParam, adaptive
    thinking, prompt cache_control). Ships in 0.4.0.

  - **openai_compat_adapter.py** (0.5.0, not yet present) — will provide
    OpenAICompatChatAdapter + OpenAICompatRewriteAdapter targeting OpenAI,
    vLLM, Ollama OpenAI-mode, LiteLLM proxies, Together, Groq, etc.
    Citations gracefully degrade (no native API on these backends);
    documents flattened into the system prompt as a context block.
"""
