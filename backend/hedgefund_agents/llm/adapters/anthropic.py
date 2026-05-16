"""Anthropic Messages API adapter. Thin wrapper over HTTPS."""
from __future__ import annotations

import time

import httpx
from django.conf import settings

from ..client import LLMResponse, Message
from ..pricing import estimate_cost

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.ANTHROPIC_API_KEY
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        self._http = http or httpx.Client(timeout=120.0)

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,  # Anthropic supports via prompt; we just hint
    ) -> LLMResponse:
        system_chunks = [m.content for m in messages if m.role == "system"]
        chat = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        body: dict[str, object] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat,
        }
        if system_chunks:
            body["system"] = "\n\n".join(system_chunks)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        t0 = time.perf_counter()
        resp = self._http.post(API_URL, json=body, headers=headers)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        resp.raise_for_status()
        payload = resp.json()
        text = "".join(
            block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"
        )
        usage = payload.get("usage", {})
        prompt_tokens = int(usage.get("input_tokens", 0))
        completion_tokens = int(usage.get("output_tokens", 0))
        cached_tokens = int(usage.get("cache_read_input_tokens", 0))
        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            cost_usd=estimate_cost(model, prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
            raw=payload,
        )
