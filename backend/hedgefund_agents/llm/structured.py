"""Structured output helper: JSON mode + Pydantic validation + 1 retry.

Some OpenRouter routes don't honor json_mode; we fall back to a strict
"return ONLY JSON" prompt and try one retry on validation failure.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from .client import LLMClient, LLMResponse, Message

# Hard ceiling on the per-call output budget the empty-content retry loop is
# allowed to escalate to. Without this, a reasoning model that burns the whole
# budget on hidden thinking tokens triggers a 4× escalation chain (8K → 32K →
# 131K) that eventually crashes against the model's context-window ceiling
# (OpenRouter returns HTTP 400 "max context 131072"). Pinning the cap at 16K
# means we still get one big-budget retry for genuinely-large outputs, but we
# never burn 40K+ output tokens chasing a model that's just churning thoughts.
MAX_ATTEMPT_TOKENS = 16_384

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class StructuredOutputError(ValueError):
    """All attempts failed schema validation.

    Subclasses ValueError so every existing ``except ValueError`` /
    ``except Exception`` handler keeps working. ``response`` carries the last
    attempt with the earlier billed attempts on ``prior_attempts``, so the
    caller can still persist the spend the provider charged for (see
    graphs/_node_fallback.py).
    """

    def __init__(self, message: str, response: LLMResponse) -> None:
        super().__init__(message)
        self.response = response


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


def call_structured[T: BaseModel](
    client: LLMClient,
    *,
    model: str,
    schema: type[T],
    messages: list[Message],
    max_tokens: int = 2048,
    temperature: float = 0.2,
    cache_ctx: dict | None = None,
) -> tuple[T, LLMResponse]:
    # L2 backtest cache: when enabled, hash (agent_name, version, model, messages, schema)
    # and short-circuit on hit. We hash the user-supplied messages BEFORE we inject the
    # schema-hint system message so the key is stable across schema_hint formatting changes.
    if cache_ctx and cache_ctx.get("enabled"):
        from apps.backtests import cache as bt_cache

        key = bt_cache.build_key(
            agent_name=cache_ctx["agent_name"],
            agent_version=cache_ctx["agent_version"],
            model=model,
            messages=messages,
            schema_name=schema.__name__,
        )
        cached = bt_cache.lookup(key)
        if cached is not None:
            bt_cache._bump("hits")
            parsed = schema.model_validate(cached)
            resp = LLMResponse(
                text=json.dumps(cached),
                model=model, provider=getattr(client, "provider", ""),
                cached_tokens=0, cost_usd=0.0, finish_reason="cache_hit",
            )
            return parsed, resp
        bt_cache._bump("misses")
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
    attempt_tokens = max_tokens
    # Every attempt below is a real, billed request. Carry the rejected ones on
    # the response we hand back so the caller records their tokens and cost too
    # (previously only the LAST attempt was ever persisted).
    billed: list[LLMResponse] = []
    for _attempt in range(3):
        resp = client.complete(
            model=model,
            messages=msgs,
            max_tokens=attempt_tokens,
            temperature=temperature,
            json_mode=True,
        )
        resp.prior_attempts = []
        billed.append(resp)
        last_resp = resp
        if not resp.text.strip():
            # Reasoning models (Qwen3, o1, etc.) can burn the full budget on
            # hidden reasoning tokens and emit empty content with
            # finish_reason="length". Retry once with a much larger budget.
            last_err = ValueError(
                f"empty content (finish_reason={resp.finish_reason!r}); "
                "likely reasoning-token exhaustion"
            )
            attempt_tokens = min(max(attempt_tokens * 4, 8192), MAX_ATTEMPT_TOKENS)
            continue
        try:
            parsed = schema.model_validate_json(_extract_json(resp.text))
            # Hand the caller every billed attempt, not just this one.
            resp.prior_attempts = billed[:-1]
            if cache_ctx and cache_ctx.get("enabled"):
                from apps.backtests import cache as bt_cache

                bt_cache.store(
                    cache_key=bt_cache.build_key(
                        agent_name=cache_ctx["agent_name"],
                        agent_version=cache_ctx["agent_version"],
                        model=model, messages=messages, schema_name=schema.__name__,
                    ),
                    agent_name=cache_ctx["agent_name"],
                    agent_version=cache_ctx["agent_version"],
                    response_json=parsed.model_dump(),
                    # Sum across billed attempts so the L2 cache row reflects
                    # what the retry chain actually cost.
                    tokens_in=sum(r.prompt_tokens for r in billed),
                    tokens_out=sum(r.completion_tokens for r in billed),
                    cost_usd=float(sum(r.cost_usd or 0.0 for r in billed)),
                )
                bt_cache._bump("writes")
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
    # Carry every billed attempt on the exception so the node wrapper can still
    # persist the spend (all three attempts were charged for).
    last_resp.prior_attempts = billed[:-1]
    raise StructuredOutputError(
        f"LLM structured output failed validation after {len(billed)} attempts: "
        f"{last_err} (model={last_resp.model}, "
        f"finish_reason={last_resp.finish_reason!r}, "
        f"completion_tokens={last_resp.completion_tokens})",
        last_resp,
    )
