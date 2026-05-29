"""Shared helpers for adapters speaking the OpenAI Chat Completions dialect.

OpenRouter and Ollama both POST to a `/chat/completions` endpoint and accept
`response_format={"type":"json_object"}`. Some routes/backends reject that
parameter outright with a non-retriable 4xx; this module centralises the
detection so both adapters can drop the hint and fall back to the prompt-side
JSON guarantee in `call_structured`.
"""
from __future__ import annotations


def is_response_format_unsupported(text: str) -> bool:
    """True when an error body says the route can't honor
    `response_format={"type":"json_object"}`.

    Canonical case: nvidia/nemotron-3-nano-30b-a3b via DeepInfra (OpenRouter)
    returns a 405 with "json_object response format is not supported for model
    ...". Matched on body text (not status code) because providers/backends
    disagree on the code — some use 400/422 for the same rejection. Requires
    BOTH a response_format mention AND a rejection phrase, so an unrelated error
    that merely contains one half never triggers the drop-and-retry fallback.
    """
    t = text.lower()
    mentions_rf = "response_format" in t or "response format" in t
    rejected = "not supported" in t or "unsupported" in t or "does not support" in t
    return mentions_rf and rejected
