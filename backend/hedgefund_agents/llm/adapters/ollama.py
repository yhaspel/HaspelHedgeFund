"""Ollama runtime adapter.

Ollama exposes an OpenAI-compatible Chat Completions endpoint at
`/v1/chat/completions`. We hit that directly so structured outputs and
JSON mode work without a special path. `host` is the base URL — usually
`http://localhost:11434` in dev or whatever the user saved in their
`ProviderKey.ollama_host`.

Local models are unpriced — cost is recorded as $0.00, which is the one
case where the `UNKNOWN_COST_SENTINEL` guard in `pricing.py` is
intentionally bypassed (zero cost is *the* known cost for local).
"""
from __future__ import annotations

import logging
import time

import httpx
from django.conf import settings

from ..client import LLMResponse, Message

log = logging.getLogger(__name__)


class OllamaClient:
    provider = "ollama"

    def __init__(self, host: str | None = None, http: httpx.Client | None = None) -> None:
        self.host = (host or getattr(settings, "OLLAMA_HOST", "") or "http://localhost:11434").rstrip("/")
        # 900s timeout: a 7-8B local model on consumer Apple Silicon can take
        # 3-5 min per structured-output call (P3-C §12.6 live-gate observation:
        # qwen2.5:7b fundamentals=193s, valuation=257s). Hosted APIs are fast
        # enough that 30s would do; the bigger cap is the local-model concession.
        self._http = http or httpx.Client(timeout=900.0)

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
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        url = f"{self.host}/v1/chat/completions"
        t0 = time.perf_counter()
        try:
            resp = self._http.post(url, json=body)
        except httpx.RequestError as e:
            raise RuntimeError(f"Ollama at {self.host} is unreachable: {e}") from e
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Ollama {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        payload = resp.json()
        choice = payload["choices"][0]
        text = choice["message"].get("content") or ""
        finish_reason = choice.get("finish_reason") or ""
        usage = payload.get("usage", {}) or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,  # local — known to be $0.
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            raw=payload,
        )
