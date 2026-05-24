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
# Non-retriable, non-transient client errors: every subsequent call to the same
# model will fail the same way. Surfaced as ModelUnavailable so prime_agent_cache
# can abort the run with a clear config error instead of accumulating silent
# graph.invoke failures until prime_min_completeness trips. 402 is the canonical
# case: OpenRouter "free" routes whose backend provider is out of credits.
MODEL_UNAVAILABLE_STATUSES = {401, 402, 403, 404}
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
        # NOTE: `json_mode=True` previously emitted `response_format={"type":
        # "json_object"}`. We dropped that because certain OpenRouter upstream
        # providers (notably Llama 3.3 70B via at least one route) re-interpret
        # it as "you should emit a tool call", then return finish_reason='tool_calls'
        # with empty content AND empty tool_calls — defeating call_structured's
        # retry loop and silently failing ~30% of agent invocations. The
        # prompt-level "respond with JSON only" instruction + schema hint that
        # call_structured already injects is sufficient and model-agnostic.
        _ = json_mode  # kwarg preserved for API stability across adapters
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/yhaspel/HaspelHedgeFund",
            "X-Title": "AIHedgeFund",
            "Content-Type": "application/json",
        }
        t0 = time.perf_counter()
        resp = self._post_with_retry(body, headers)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code in MODEL_UNAVAILABLE_STATUSES:
            from apps.backtests.exceptions import ModelUnavailable

            raise ModelUnavailable(model=model, status_code=resp.status_code, body=resp.text)
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
        # Some OpenRouter upstreams (notably Llama-3.3-70B-Instruct via certain
        # providers) interpret our `response_format: json_object` request as
        # "you should emit a tool call" and return finish_reason='tool_calls'
        # with content=null. The structured JSON lives in
        # message.tool_calls[0].function.arguments — surface it as the response
        # text so call_structured can parse it. We don't care which function
        # name the provider invented; we only requested JSON.
        if not text and finish_reason == "tool_calls":
            tool_calls = choice["message"].get("tool_calls") or []
            if tool_calls:
                args = tool_calls[0].get("function", {}).get("arguments")
                if isinstance(args, str) and args.strip():
                    text = args
                    # Re-stamp finish_reason so structured.py treats this as a
                    # normal "stop" — otherwise its empty-content branch fires.
                    finish_reason = "stop"
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
