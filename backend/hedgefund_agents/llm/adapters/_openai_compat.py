"""Shared helpers for adapters speaking the OpenAI Chat Completions dialect.

OpenRouter and Ollama both POST to a `/chat/completions` endpoint and accept
`response_format={"type":"json_object"}`. Some routes/backends reject that
parameter outright with a non-retriable 4xx; this module centralises the
detection so both adapters can drop the hint and fall back to the prompt-side
JSON guarantee in `call_structured`.
"""
from __future__ import annotations

from ..client import Message


def to_openai_messages(messages: list[Message]) -> list[dict[str, str]]:
    """Render `Message`s into OpenAI chat dicts, merging consecutive same-role turns.

    `call_structured` prepends a schema-hint `system` message ahead of each
    agent's own `system` prompt, yielding `[system, system, user]`. The OpenAI
    Chat Completions spec permits multiple system messages, but several
    OpenRouter upstreams (observed run 198: Phala and Parasail serving
    Llama/Qwen via vLLM) apply a chat template that extracts only `messages[0]`
    as the system turn and then enforces strict user/assistant alternation on
    the remainder — so the second consecutive `system` trips a 400
    "Conversation roles must alternate user/assistant/user/assistant".

    The Anthropic adapter sidesteps this by hoisting every system chunk into its
    dedicated top-level `system` field; we do the equivalent for the OpenAI
    dialect by joining adjacent same-role turns with a blank line. This also
    hardens the validation-retry path (and any future caller) against emitting
    two same-role turns in a row.
    """
    out: list[dict[str, str]] = []
    for m in messages:
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] = f"{out[-1]['content']}\n\n{m.content}"
        else:
            out.append({"role": m.role, "content": m.content})
    return out


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
