"""Structured output helper: JSON mode + Pydantic validation + 1 retry.

Some OpenRouter routes don't honor json_mode; we fall back to a strict
"return ONLY JSON" prompt and try one retry on validation failure.
"""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .client import LLMClient, LLMResponse, Message

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str) -> str:
    """Pull JSON out of fenced code blocks or raw text."""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return text[s : e + 1]
    return text.strip()


def call_structured(
    client: LLMClient,
    *,
    model: str,
    schema: type[T],
    messages: list[Message],
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> tuple[T, LLMResponse]:
    schema_hint = json.dumps(schema.model_json_schema(), indent=2)
    system_addendum = Message(
        role="system",
        content=(
            "You MUST respond with a single JSON object matching this schema. "
            "Do not include prose, explanations, or markdown fences. JSON only.\n\n"
            f"SCHEMA:\n{schema_hint}"
        ),
    )
    msgs = [system_addendum, *messages]
    last_err: Exception | None = None
    last_resp: LLMResponse | None = None
    for attempt in range(2):
        resp = client.complete(
            model=model,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=True,
        )
        last_resp = resp
        try:
            parsed = schema.model_validate_json(_extract_json(resp.text))
            return parsed, resp
        except (ValidationError, json.JSONDecodeError) as e:
            last_err = e
            msgs = [
                *msgs,
                Message(role="assistant", content=resp.text),
                Message(
                    role="user",
                    content=(
                        f"Your previous response did not match the schema: {e}. "
                        "Respond again with valid JSON only."
                    ),
                ),
            ]
    assert last_resp is not None
    raise ValueError(
        f"LLM structured output failed validation after 2 attempts: {last_err}"
    )
