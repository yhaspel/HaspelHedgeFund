"""OpenRouter adapter — speaks the OpenAI Chat Completions dialect."""
from __future__ import annotations

import json
import logging
import random
import time

import httpx
from django.conf import settings

from ..client import LLMResponse, Message
from ..pricing import estimate_cost
from ._openai_compat import is_response_format_unsupported, to_openai_messages

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
# Cap on how long a single 408/429/5xx retry will sleep. OpenRouter's Retry-After
# for a saturated :free pool (~29s) is an optimistic upstream hint, not a
# guarantee the limit clears — honoring it unclamped meant 5 retries could block
# the worker ~147s per model (~295s across a fallback hop) for every synchronous
# agent call. Clamp so a congested pool can't stall a run for minutes; the
# fallback chain + terminal RateLimited handle the case where it won't clear.
RETRY_AFTER_CAP_SECONDS = 8.0

# Reasoning models spend their token budget on hidden thinking and can return
# empty content under the default budget (run 151: gpt-oss-120b:free returned
# finish_reason='', completion_tokens=91, empty content). Capping reasoning
# effort steers tokens toward the visible answer. Gated by slug so non-reasoning
# routes (e.g. the prod Llama-3.3-70B analytical default) never get an extra
# `reasoning` param that some providers reject with a 400.
#
# Fragments are matched as substrings, so they MUST be specific enough not to
# catch a non-reasoning sibling: "deepseek-v4-pro" (NOT "deepseek-v4", which
# would wrongly flag the frugal workhorse deepseek-v4-flash) and
# "nemotron-3-nano-omni" (NOT "nemotron", which would flag the frugal
# nemotron-3-nano-30b-a3b). This tuple is the single source of truth for what
# counts as a reasoning model: it drives the effort cap here, the catalog's
# ModelEntry.supports_reasoning flag (seed + the §reasoning seed invariant), and
# the "no reasoning model on a decision role / cheap-tier menu" guards in
# tests/test_preset_invariants.py.
_REASONING_SLUGS = (
    "gpt-oss", "o1", "o3", "deepseek-r1", "qwen3",
    "deepseek-v4-pro", "glm-5.1", "nemotron-3-nano-omni",
)


def is_reasoning_slug(model_id: str) -> bool:
    """True if `model_id` names a dedicated reasoning model (per _REASONING_SLUGS).

    Accepts a bare slug ("deepseek/deepseek-r1") or a provider-qualified
    ModelEntry id ("openrouter:deepseek/deepseek-r1") — the provider prefix
    carries no reasoning fragments, so the substring test is unaffected.
    """
    return any(tag in model_id.lower() for tag in _REASONING_SLUGS)

# Same-price-tier fallback when a model STILL returns empty content. Free slugs
# fall back to another free slug so we never silently escalate cost.
_EMPTY_CONTENT_FALLBACK = {
    "openai/gpt-oss-120b:free": "meta-llama/llama-3.3-70b-instruct:free",
}

# Same-tier fallback when a route's transient-failure RETRIES ARE EXHAUSTED —
# typically a persistently rate-limited :free upstream (run 152: llama-3.3-70b
# :free via Venice returned 429 past the ~62s retry budget). Targets a non-
# reasoning free slug on a DIFFERENT upstream provider so we ride out one
# provider's rate limit without escalating cost. Hermes-3-405B is non-reasoning
# (avoids the gpt-oss empty-content trap) and routed off Venice. The visited-set
# guard (_tried) lets this compose with the empty-content hop above without ever
# looping: gpt-oss(empty)→llama(429)→hermes, each model tried at most once.
_RATE_LIMIT_FALLBACK = {
    "meta-llama/llama-3.3-70b-instruct:free": "nousresearch/hermes-3-llama-3.1-405b:free",
    "openai/gpt-oss-120b:free": "nousresearch/hermes-3-llama-3.1-405b:free",
}

# Opt-in PAID escape hatch (settings.OPENROUTER_PAID_FALLBACK, default off). The
# same-tier free fallbacks above all draw the SAME shared account/upstream :free
# pool, so when that pool is saturated they 429 in lockstep and the chain dead-
# ends. With the flag on, hop ONCE from any :free route to the cheap paid
# analytical default — a non-:free route that draws the account's PAID quota and
# so escapes the free pool — rather than failing the run. Default off so dev
# (BLOCK_ANTHROPIC / zero-spend) never silently bills. $0.40/Mtok, already the
# prod analytical default (registry._PROD_ANALYTICAL) and in MODEL_CATALOG.
_PAID_FALLBACK_TARGET = "meta-llama/llama-3.3-70b-instruct"

log = logging.getLogger(__name__)


def _last_resort_model() -> str:
    """The known-good NON-reasoning model the self-heal layer (L2) hops to when a
    configured route is dead (404/402), returns a non-JSON body, or is terminally
    empty. Defaults to the prod analytical default (Llama 3.3 70B). Under
    LLM_FREE_ONLY, use the :free variant so zero-spend environments never bill."""
    target = getattr(
        settings, "LLM_LAST_RESORT_MODEL", "meta-llama/llama-3.3-70b-instruct"
    )
    if getattr(settings, "LLM_FREE_ONLY", False) and not target.endswith(":free"):
        target = f"{target}:free"
    return target


class OpenRouterClient:
    provider = "openrouter"

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        # L1: a per-operation httpx.Timeout, not a scalar. The scalar 120s was a
        # per-READ timeout that a trickling reasoning route reset on every byte
        # and so never fired (run 236 parked the worker 10+ min). A tighter read
        # timeout turns a stalled response into a normal httpx.ReadTimeout that
        # the retry / self-heal path handles; connect/write/pool stay bounded too.
        read_timeout = float(getattr(settings, "LLM_HTTP_READ_TIMEOUT", 45.0))
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0, read=read_timeout, write=10.0, pool=10.0
            )
        )
        # Models whose route rejected response_format=json_object outright. The
        # client is lru_cache'd (registry._make_client) and reused across every
        # agent call in a run, so memoizing here lets the rest of the run skip
        # the doomed first attempt instead of eating a 4xx + retry per call.
        self._no_response_format: set[str] = set()

    def _self_heal(
        self,
        *,
        reason: str,
        model: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        _tried: tuple[str, ...],
    ) -> LLMResponse | None:
        """L2 self-heal: hop ONCE to the known-good non-reasoning last-resort
        model when `model` is dead/empty/non-JSON. Returns the recovered
        response, or None when no hop is possible (self-heal off, or the dead
        model already IS the last resort, or it was already tried) — the caller
        then falls back to its terminal raise. `_tried` makes this loop-proof:
        the dead model is added before recursing, so the last resort is attempted
        at most once and a broken account still surfaces a real error."""
        if not getattr(settings, "LLM_SELF_HEAL", True):
            return None
        target = _last_resort_model()
        if target == model or target in _tried:
            return None
        log.warning(
            "openrouter self-heal: %s (model=%s); hopping to last-resort %s",
            reason, model, target,
        )
        return self.complete(
            model=target,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            _tried=_tried + (model,),
        )

    def _json_with_retry(
        self, resp: httpx.Response, body: dict, headers: dict, attempts: int = 2
    ) -> dict | None:
        """Parse `resp` as JSON, re-POSTing on a non-JSON 2xx body (Bug C).

        A 2xx whose body fails to parse is treated as a transient provider
        hiccup and the same request is re-sent up to `attempts` times. Returns
        the parsed dict on success, or None when every attempt yields a
        non-JSON / non-2xx body (the caller then self-heals or raises)."""
        for i in range(attempts + 1):
            if resp.status_code < 400:
                try:
                    return resp.json()
                except (json.JSONDecodeError, ValueError):
                    pass  # non-JSON 2xx — fall through to a re-POST
            if i == attempts:
                break
            log.warning(
                "openrouter non-JSON 2xx body (model=%s, status=%s); "
                "re-POSTing (attempt %d/%d)",
                body.get("model"), resp.status_code, i + 1, attempts,
            )
            resp = self._post_with_retry(body, headers)
        return None

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
        _tried: tuple[str, ...] = (),
    ) -> LLMResponse:
        want_json_object = json_mode and model not in self._no_response_format
        body = self._build_body(model, messages, max_tokens, temperature, want_json_object)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/yhaspel/HaspelHedgeFund",
            "X-Title": "AIHedgeFund",
            "Content-Type": "application/json",
        }
        t0 = time.perf_counter()
        try:
            resp = self._post_with_retry(body, headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # L1→L2: the route stalled/failed at the transport layer past the
            # retry budget. Hop once to the non-reasoning last resort rather than
            # bubbling a hang/timeout up to the run (run 236 class).
            healed = self._self_heal(
                reason=f"transport error ({type(exc).__name__})",
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=temperature, json_mode=json_mode, _tried=_tried,
            )
            if healed is not None:
                return healed
            raise
        # Some OpenRouter routes (e.g. nvidia/nemotron-3-nano via DeepInfra)
        # reject response_format=json_object with a non-retriable 4xx instead of
        # honoring or mis-reading it. call_structured's schema-hint system message
        # already forces JSON-only output, so we drop the request hint and retry
        # once. Memoize so subsequent calls to this model skip the doomed attempt.
        rejected_rf = resp.status_code >= 400 and is_response_format_unsupported(resp.text)
        if want_json_object and rejected_rf:
            log.warning(
                "openrouter %s rejected response_format=json_object; "
                "retrying without it (model=%s)",
                resp.status_code,
                model,
            )
            self._no_response_format.add(model)
            body = self._build_body(model, messages, max_tokens, temperature, False)
            resp = self._post_with_retry(body, headers)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        # Retries inside _post_with_retry are exhausted and the route is still
        # failing transiently (typically a rate-limited :free upstream). Hop once
        # to a same-tier free model on a different provider rather than failing
        # the whole run. Guarded by _tried so the chain can't loop.
        if resp.status_code in RETRY_STATUSES:
            fb = _RATE_LIMIT_FALLBACK.get(model)
            if fb and fb not in _tried:
                log.warning(
                    "openrouter %s exhausted retries for %s; falling back to %s",
                    resp.status_code,
                    model,
                    fb,
                )
                return self.complete(
                    model=fb,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                    _tried=_tried + (model,),
                )
            # Free same-tier fallbacks exhausted. With the opt-in paid escape
            # hatch on, hop once from a :free route to the paid analytical
            # default, which draws the account's paid quota instead of the
            # saturated shared free pool. _tried guards against re-hopping.
            if (
                getattr(settings, "OPENROUTER_PAID_FALLBACK", False)
                and model.endswith(":free")
                and _PAID_FALLBACK_TARGET not in _tried
            ):
                log.warning(
                    "openrouter %s exhausted free routes for %s; "
                    "OPENROUTER_PAID_FALLBACK on — escalating to paid %s",
                    resp.status_code,
                    model,
                    _PAID_FALLBACK_TARGET,
                )
                return self.complete(
                    model=_PAID_FALLBACK_TARGET,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                    _tried=_tried + (model,),
                )
            # 429 with retries AND every configured fallback exhausted: the
            # shared :free pool is saturated and a hop won't escape it. Raise a
            # typed, actionable error so the run aborts ONCE (live: a clean
            # FAILED message instead of a raw HTTPStatusError stack trace;
            # backtest: abort instead of null-signalling every council) rather
            # than dead-ending into the generic 4xx raise below.
            if resp.status_code == 429:
                from apps.backtests.exceptions import RateLimited

                raise RateLimited(model=model, body=resp.text)
        if resp.status_code in MODEL_UNAVAILABLE_STATUSES:
            # Bug A (runs 228-235): a removed/dead route (404) or out-of-credits
            # (402) used to fail the WHOLE run. Self-heal first: hop once to the
            # known-good non-reasoning model so one dead agent doesn't kill the
            # council. Only if the last resort is unreachable too do we raise.
            healed = self._self_heal(
                reason=f"model unavailable (HTTP {resp.status_code})",
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=temperature, json_mode=json_mode, _tried=_tried,
            )
            if healed is not None:
                return healed
            from apps.backtests.exceptions import ModelUnavailable

            raise ModelUnavailable(model=model, status_code=resp.status_code, body=resp.text)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"OpenRouter {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        # Bug C (run 241): a 2xx whose body is NOT JSON (an SSE fragment, a
        # truncated body, or a provider HTML/plain-text error page) used to raise
        # an unhandled JSONDecodeError and kill the run. A non-JSON 2xx is almost
        # always a transient provider hiccup, so re-POST the same model a couple
        # of times; if it persists, self-heal to the last resort; only then raise.
        payload = self._json_with_retry(resp, body, headers)
        if payload is None:
            healed = self._self_heal(
                reason="non-JSON 2xx response body",
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=temperature, json_mode=json_mode, _tried=_tried,
            )
            if healed is not None:
                return healed
            raise RuntimeError(
                f"OpenRouter returned a non-JSON body for {model!r} after retries "
                f"(status {resp.status_code}): {resp.text[:300]!r}"
            )
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
        # Some providers (observed: Parasail for meta-llama/llama-3.3-70b-instruct)
        # answer response_format=json_object with a phantom finish_reason='tool_calls'
        # and NO content AND NO tool_calls — the generated tokens are silently
        # dropped (run 155). The SAME providers return clean JSON when
        # response_format is omitted, and call_structured's schema-hint system
        # message already forces JSON-only output. Drop the hint and retry once;
        # memoize so the rest of the run skips the doomed format. want_json_object
        # is only True on the first pass, so this fires at most once per model.
        if not text and want_json_object and model not in self._no_response_format:
            log.warning(
                "openrouter empty content from %s with response_format "
                "(finish_reason=%r, provider=%r); retrying without it",
                model,
                finish_reason,
                payload.get("provider"),
            )
            self._no_response_format.add(model)
            return self.complete(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=json_mode,
                _tried=_tried,
            )
        # Reasoning models can return empty content even after the caller's
        # token-budget escalation (the budget goes to hidden thinking). Fall
        # back once to a same-price-tier model rather than failing the run.
        if not text:
            fb = _EMPTY_CONTENT_FALLBACK.get(model)
            if fb and fb not in _tried:
                log.warning(
                    "openrouter empty content from %s (finish_reason=%r); "
                    "falling back to same-tier %s",
                    model,
                    finish_reason,
                    fb,
                )
                return self.complete(
                    model=fb,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                    _tried=_tried + (model,),
                )
            # Bug B (runs 236/237): a reasoning route (e.g. qwen/qwen3.6-27b) burns
            # its budget on hidden thinking and returns empty content with no
            # same-tier entry above. Self-heal to the non-reasoning last resort so
            # the agent still produces a real signal instead of a null one.
            healed = self._self_heal(
                reason=f"terminal empty content (finish_reason={finish_reason!r})",
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=temperature, json_mode=json_mode, _tried=_tried,
            )
            if healed is not None:
                return healed
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

    def _build_body(
        self,
        model: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
        json_object: bool,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": model,
            "messages": to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # `json_object=True` emits `response_format={"type": "json_object"}` so
        # OpenAI-compatible upstreams that respect it produce parseable JSON, and
        # so cassette-replay integration tests (which recorded request bodies with
        # this field) keep matching. Routes that mis-read it as "emit a tool call"
        # are handled by the response-side tool_calls→content fallback in
        # complete(); routes that reject it outright are handled by the caller's
        # retry-without-it fallback.
        if json_object:
            body["response_format"] = {"type": "json_object"}
        if is_reasoning_slug(model):
            body["reasoning"] = {"effort": "low"}
        return body

    def _post_with_retry(self, body: dict, headers: dict) -> httpx.Response:
        """Exponential backoff on 408/429/5xx, mirroring AnthropicClient.

        OpenRouter exposes the same transient-failure classes (rate-limited,
        upstream provider hiccup); keeping retry semantics identical means
        the agent layer sees one consistent retry budget regardless of which
        provider the model is routed through.
        """
        last: httpx.Response | None = None
        for attempt in range(MAX_RETRIES + 1):
            # L1: a stalled route now trips httpx's tightened read timeout as a
            # TimeoutException instead of parking the worker indefinitely (run
            # 236). Treat transport errors like a transient 5xx — back off and
            # retry; if the budget is exhausted, re-raise so complete()'s
            # transport-error handler can self-heal to the last-resort model.
            try:
                resp = self._http.post(API_URL, json=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == MAX_RETRIES:
                    raise
                wait = min(
                    BASE_BACKOFF_SECONDS * (2 ** attempt), RETRY_AFTER_CAP_SECONDS
                ) + random.uniform(0, 0.5)
                log.warning(
                    "openrouter transport error %s on attempt %d; sleeping %.1fs",
                    type(exc).__name__, attempt + 1, wait,
                )
                time.sleep(wait)
                continue
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
            # Clamp: a saturated :free pool's Retry-After is an optimistic hint,
            # not a guarantee — honoring it unclamped blocked the worker for
            # minutes per call. Cap the sleep; the fallback chain handles the rest.
            wait = min(wait, RETRY_AFTER_CAP_SECONDS) + random.uniform(0, 0.5)
            log.warning(
                "openrouter %s on attempt %d; sleeping %.1fs",
                resp.status_code,
                attempt + 1,
                wait,
            )
            time.sleep(wait)
        assert last is not None
        return last
