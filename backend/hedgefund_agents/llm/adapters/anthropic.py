"""Anthropic Messages API adapter. Thin wrapper over HTTPS."""
from __future__ import annotations

import logging
import random
import time

import httpx
from django.conf import settings

from ..client import LLMResponse, Message
from ..pricing import estimate_cost

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 529}
# Permanently-broken config for THIS model/key: a bad key, no credit, no access,
# or a model id that doesn't exist. Every subsequent call fails identically, so
# these must surface as ModelUnavailable (which the node self-heal wrapper
# re-raises) instead of a generic HTTPStatusError that degrades every agent to a
# neutral signal and lets the run "complete" with all-hold garbage. Mirrors
# openrouter.MODEL_UNAVAILABLE_STATUSES.
MODEL_UNAVAILABLE_STATUSES = {401, 402, 403, 404}
# Anthropic's shared-capacity signals: 429 rate limit and 529 "overloaded".
CAPACITY_STATUSES = {429, 529}
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2.0
# Cap on a single retry sleep — mirrors OpenRouterClient so the agent layer sees
# one consistent retry budget regardless of provider. Bounds a long upstream
# Retry-After so an overloaded API can't stall the worker for minutes per call.
RETRY_AFTER_CAP_SECONDS = 8.0

log = logging.getLogger(__name__)


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        if getattr(settings, "OFFLINE_MODE", False):  # P4-OFF WS-1.8: defense in depth
            from hedgefund_agents.errors import OfflineLLMViolation

            raise OfflineLLMViolation(
                "AnthropicClient must not be constructed in OFFLINE_MODE — "
                "offline runs use the local Ollama model only."
            )
        if getattr(settings, "BLOCK_ANTHROPIC", False):
            raise RuntimeError(
                "Anthropic API is blocked in this environment "
                "(settings.BLOCK_ANTHROPIC=True). Route this agent through "
                "OpenRouter or unset BLOCK_ANTHROPIC."
            )
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
        resp = self._post_with_retry(body, headers)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._raise_for_status(model, resp)
        payload = resp.json()
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
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

    @staticmethod
    def _raise_for_status(model: str, resp: httpx.Response) -> None:
        """Map Anthropic HTTP errors onto the same classes OpenRouter raises.

        Before this, every error status fell through ``resp.raise_for_status()``
        as a generic ``httpx.HTTPStatusError`` — which ``wrap_backtest_tolerant``
        treats as "one flaky agent" and degrades. With Anthropic as the default
        route for all 8 personas + risk + CIO, a single bad key therefore
        produced a DONE run whose entire council was a silent neutral/hold.

        - 401/402/403/404  → ``ModelUnavailable`` (non-retryable config error);
        - terminal 429/529 → ``RateLimited`` (capacity exhausted for this run);
        - terminal 5xx     → ``httpx.HTTPStatusError``, same as OpenRouter, so a
          genuinely transient upstream blip still self-heals per agent;
        - other 4xx        → ``httpx.HTTPStatusError`` (bad request: our bug).
        """
        if resp.status_code < 400:
            return
        from apps.backtests.exceptions import ModelUnavailable, RateLimited

        if resp.status_code in MODEL_UNAVAILABLE_STATUSES:
            raise ModelUnavailable(
                model=model, status_code=resp.status_code, body=resp.text
            )
        if resp.status_code in CAPACITY_STATUSES:
            # _post_with_retry already burned MAX_RETRIES of backoff on these.
            raise RateLimited(model=model, body=resp.text)
        resp.raise_for_status()

    def _post_with_retry(self, body: dict, headers: dict) -> httpx.Response:
        """Exponential backoff on 408/429/5xx including 529 overload.

        Honors `retry-after` if Anthropic returns one. Per Anthropic guidance,
        these are transient and should be retried."""
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
            wait = min(wait, RETRY_AFTER_CAP_SECONDS) + random.uniform(0, 0.5)  # clamp + jitter
            log.warning(
                "anthropic %s on attempt %d; sleeping %.1fs", resp.status_code, attempt + 1, wait
            )
            time.sleep(wait)
        assert last is not None
        return last
