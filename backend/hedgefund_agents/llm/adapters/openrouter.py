"""OpenRouter adapter — speaks the OpenAI Chat Completions dialect."""
from __future__ import annotations

import logging
import random
import time

import httpx
from django.conf import settings

from ..client import LLMResponse, Message
from ..pricing import estimate_cost

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2.0

log = logging.getLogger(__name__)


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
        resp = self._post_with_retry(body, headers)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"OpenRouter {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        payload = resp.json()
        if "choices" not in payload:
            # OpenRouter returns HTTP 200 with {"error": {...}} for upstream
            # provider failures (rate limit, model unavailable, content
            # policy). Raise so call_structured retries / surfaces it.
            err = payload.get("error") or payload
            raise RuntimeError(f"OpenRouter response missing 'choices': {err}")
        choice = payload["choices"][0]
        text = choice["message"].get("content") or ""
        finish_reason = choice.get("finish_reason") or ""
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
            finish_reason=finish_reason,
            raw=payload,
        )

    def _post_with_retry(self, body: dict, headers: dict) -> httpx.Response:
        """Exponential backoff on 408/429/5xx, mirroring AnthropicClient.

        OpenRouter exposes the same transient-failure classes (rate-limited,
        upstream provider hiccup); keeping retry semantics identical means
        the agent layer sees one consistent retry budget regardless of which
        provider the model is routed through.
        """
        last: httpx.Response | None = None
        for attempt in range(MAX_RETRIES + 1):
            resp = self._http.post(API_URL, json=body, headers=headers)
            if resp.status_code not in RETRY_STATUSES:
                return resp
            last = resp
            if attempt == MAX_RETRIES:
                return resp
            retry_after = resp.headers.get("retry-after")
            try:
                wait = float(retry_after) if retry_after else BASE_BACKOFF_SECONDS * (2 ** attempt)
            except ValueError:
                wait = BASE_BACKOFF_SECONDS * (2 ** attempt)
            wait += random.uniform(0, 0.5)
            log.warning(
                "openrouter %s on attempt %d; sleeping %.1fs",
                resp.status_code,
                attempt + 1,
                wait,
            )
            time.sleep(wait)
        assert last is not None
        return last
