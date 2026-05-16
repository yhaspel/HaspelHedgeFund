"""OpenRouter adapter — speaks the OpenAI Chat Completions dialect."""
from __future__ import annotations

import time

import httpx
from django.conf import settings

from ..client import LLMResponse, Message
from ..pricing import estimate_cost

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterClient:
    provider = "openrouter"

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        self._http = http or httpx.Client(timeout=120.0)

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        body: dict[str, object] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/yhaspel/HaspelHedgeFund",
            "X-Title": "AIHedgeFund",
            "Content-Type": "application/json",
        }
        t0 = time.perf_counter()
        resp = self._http.post(API_URL, json=body, headers=headers)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"OpenRouter {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        payload = resp.json()
        text = payload["choices"][0]["message"]["content"] or ""
        usage = payload.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimate_cost(model, prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
            raw=payload,
        )
