"""OpenRouter adapter unit tests.

The adapter has two non-obvious paths that have bitten us:
- 401/402/403/404 → typed `ModelUnavailable` (separate suite)
- finish_reason='tool_calls' with empty content → extract JSON from
  tool_calls[0].function.arguments instead of treating as empty-content retry.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from apps.backtests.exceptions import ModelUnavailable, RateLimited
from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient
from hedgefund_agents.llm.client import Message

_LAST_RESORT = "meta-llama/llama-3.3-70b-instruct"


def _resp(payload: dict, status_code: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = text
    resp.request = MagicMock()
    return resp


def _nonjson_resp(text: str = "data: {...}\n\ndata: [DONE]\n", status_code: int = 200) -> MagicMock:
    """A 2xx whose body is NOT valid JSON (Bug C, run 241): .json() raises."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.side_effect = json.JSONDecodeError("Expecting value", text, 0)
    resp.text = text
    resp.request = MagicMock()
    return resp


def _fake_http(payload: dict, status_code: int = 200) -> MagicMock:
    http = MagicMock()
    http.post.return_value = _resp(payload, status_code)
    return http


def _seq_http(*responses: MagicMock) -> MagicMock:
    """HTTP whose successive .post() calls return the given responses in order."""
    http = MagicMock()
    http.post.side_effect = list(responses)
    return http


# Real DeepInfra rejection body for nvidia/nemotron-3-nano-30b-a3b, as surfaced
# through OpenRouter's error wrapper.
_RF_REJECTION_TEXT = (
    '{"error":{"message":"json_object response format is not supported for '
    'model: nvidia/Nemotron-3-Nano-30B-A3B","type":"invalid_request_error",'
    '"param":"response_format","code":null}}'
)


def _build_client(http) -> OpenRouterClient:
    return OpenRouterClient(api_key="test-key", http=http)


def test_extracts_content_from_normal_response():
    """Sanity check: usual {content: "..."} path still works."""
    http = _fake_http(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": '{"signal":"buy","confidence":80}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
    )
    resp = _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
    )
    assert resp.text == '{"signal":"buy","confidence":80}'
    assert resp.finish_reason == "stop"


def test_falls_back_to_tool_calls_arguments_when_content_empty():
    """Regression for bt15 aborted_partial: Llama 3.3 70B on certain OpenRouter
    upstreams returns finish_reason='tool_calls' with content=null instead of
    emitting JSON directly. The structured JSON lives in
    tool_calls[0].function.arguments — adapter must surface it as the text
    payload so call_structured can parse it instead of triggering the
    empty-content retry."""
    args_json = '{"signal":"hold","confidence":50,"thesis":"flat","key_risks":[]}'
    http = _fake_http(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "emit_json", "arguments": args_json},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
    )
    resp = _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
    )
    assert resp.text == args_json
    # finish_reason is re-stamped to "stop" so structured.py treats it as a
    # normal completion, not an empty-content retry candidate.
    assert resp.finish_reason == "stop"


def test_tool_calls_with_empty_arguments_does_not_overwrite():
    """If tool_calls exists but arguments is empty/whitespace, leave the text
    empty so call_structured's existing retry kicks in (we don't want to feed
    the parser an empty string and trigger a misleading JSONDecodeError)."""
    http = _fake_http(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "function": {"name": "emit_json", "arguments": "   "},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 0},
        }
    )
    resp = _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
    )
    assert resp.text == ""
    assert resp.finish_reason == "tool_calls"  # NOT re-stamped


def test_sends_response_format_json_object_when_json_mode_true():
    """`json_mode=True` must put `response_format={"type":"json_object"}` on
    the request body. OpenAI-compatible upstreams that respect it produce
    parseable JSON; the cassette-replay integration tests recorded request
    bodies with this field present (removing it broke cassette matching for
    test_full_run_aapl_qwen). The Llama-3.3-70B "tool_calls" misbehavior is
    handled response-side by the tool_calls→content fallback below."""
    http = _fake_http(
        {
            "choices": [
                {
                    "message": {"content": '{"ok":true}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
    )
    _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
        json_mode=True,
    )
    body = http.post.call_args.kwargs["json"]
    assert body.get("response_format") == {"type": "json_object"}


def test_omits_response_format_when_json_mode_false():
    """Negative half of the regression: when json_mode=False, the request
    body must NOT carry `response_format` (so plain-text completions still
    work for callers that didn't ask for JSON)."""
    http = _fake_http(
        {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }
    )
    _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
        json_mode=False,
    )
    body = http.post.call_args.kwargs["json"]
    assert "response_format" not in body


def test_tool_calls_with_content_present_does_not_overwrite():
    """If both content and tool_calls are present, prefer content (the user
    explicitly asked for json_object and got it; no need to walk tool_calls)."""
    http = _fake_http(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"signal":"sell"}',
                        "tool_calls": [
                            {
                                "function": {"name": "emit_json", "arguments": '{"signal":"buy"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
    )
    resp = _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
    )
    assert resp.text == '{"signal":"sell"}'
    assert resp.finish_reason == "tool_calls"  # unchanged; content takes precedence


def test_retries_without_response_format_when_route_rejects_json_object():
    """Regression for the nvidia/nemotron-3-nano DeepInfra 405: the route rejects
    response_format=json_object outright. call_structured's schema-hint already
    forces JSON-only output, so the adapter must drop the request hint and retry
    once rather than surfacing the 405 as a fatal HTTPStatusError."""
    rejection = _resp({}, status_code=405, text=_RF_REJECTION_TEXT)
    success = _resp(
        {
            "choices": [
                {
                    "message": {"content": '{"signal":"buy"}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 80, "completion_tokens": 12},
        }
    )
    http = _seq_http(rejection, success)
    resp = _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
        json_mode=True,
    )
    assert resp.text == '{"signal":"buy"}'
    assert resp.finish_reason == "stop"
    assert http.post.call_count == 2
    # First attempt carried response_format; the retry dropped it.
    assert http.post.call_args_list[0].kwargs["json"]["response_format"] == {"type": "json_object"}
    assert "response_format" not in http.post.call_args_list[1].kwargs["json"]


def test_memoizes_unsupported_model_and_skips_response_format_next_call():
    """After a route is learned to reject response_format, subsequent calls to the
    same model on the same (lru_cache'd) client must skip it proactively — no
    repeated 405 + retry per call."""
    rejection = _resp({}, status_code=405, text=_RF_REJECTION_TEXT)
    success = _resp(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
    )
    success2 = _resp(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
    )
    http = _seq_http(rejection, success, success2)
    client = _build_client(http)
    # A priced slug (the rejection is detected from the response body, not the
    # model id — nvidia/nemotron-3-nano is the real-world trigger but is priced
    # via the catalog DB, absent in unit tests).
    kwargs = {
        "model": "meta-llama/llama-3.3-70b-instruct",
        "messages": [Message("user", "hi")],
        "json_mode": True,
    }
    client.complete(**kwargs)  # learns: rejection (405) then retry (success)
    client.complete(**kwargs)  # memoized: single request, no response_format
    assert http.post.call_count == 3
    # The third request (the second call's only one) never sent response_format.
    assert "response_format" not in http.post.call_args_list[2].kwargs["json"]


def test_unrelated_4xx_still_raises_and_does_not_retry():
    """A 4xx that is NOT about response_format (here a generic 405) must keep its
    current behavior: surface as HTTPStatusError, no silent retry-without-hint."""
    http = _seq_http(_resp({}, status_code=405, text="Method Not Allowed"))
    with pytest.raises(httpx.HTTPStatusError):
        _build_client(http).complete(
            model="meta-llama/llama-3.3-70b-instruct",
            messages=[Message("user", "hi")],
            json_mode=True,
        )
    assert http.post.call_count == 1


def test_coalesces_consecutive_system_messages():
    """Regression for run 198: call_structured prepends a schema-hint `system`
    message ahead of each agent's own `system` prompt, so the adapter receives
    [system, system, user]. Strict OpenRouter upstreams (Phala/Parasail via
    vLLM) extract only messages[0] as the system turn and then enforce strict
    user/assistant alternation on the rest, rejecting the second `system` with a
    400 "Conversation roles must alternate user/assistant/...". The adapter must
    merge the two system turns into one before sending."""
    http = _fake_http(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
    )
    _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[
            Message("system", "SCHEMA: {...}"),
            Message("system", "You are Buffett."),
            Message("user", "Analyze AAPL."),
        ],
    )
    sent = http.post.call_args.kwargs["json"]["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[0]["content"] == "SCHEMA: {...}\n\nYou are Buffett."
    assert sent[1]["content"] == "Analyze AAPL."


def test_preserves_alternating_messages_unchanged():
    """Coalescing must be a no-op for already-alternating turns — the
    validation-retry path (system, user, assistant, user) must pass through with
    every turn intact."""
    http = _fake_http(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
    )
    _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[
            Message("system", "sys"),
            Message("user", "first"),
            Message("assistant", "reply"),
            Message("user", "retry"),
        ],
    )
    sent = http.post.call_args.kwargs["json"]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in sent] == ["sys", "first", "reply", "retry"]


def test_reasoning_effort_set_for_reasoning_slugs_only():
    """gpt-oss (and other reasoning slugs) get `reasoning={"effort":"low"}` so
    the token budget goes to the visible answer instead of hidden thinking
    (run 151 returned empty content, completion_tokens=91). Non-reasoning
    routes like the prod Llama-3.3-70B analytical default must NOT get the
    param — some providers reject unknown fields with a 400."""
    http = _fake_http(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
    )
    client = _build_client(http)
    client.complete(model="openai/gpt-oss-120b:free", messages=[Message("user", "hi")])
    assert http.post.call_args.kwargs["json"]["reasoning"] == {"effort": "low"}

    http2 = _fake_http(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
    )
    _build_client(http2).complete(
        model="meta-llama/llama-3.3-70b-instruct", messages=[Message("user", "hi")]
    )
    assert "reasoning" not in http2.post.call_args.kwargs["json"]


# 2026-10-20 dev-tier ladder (tier_menus.DEV_TIER_SLUGS). The adapter's static
# maps (_EMPTY_CONTENT_FALLBACK / _RATE_LIMIT_FALLBACK) and its static :free
# ladder are all drawn from it, so these tests pin the ladder explicitly: a
# re-curation that breaks the chain shape must be a conscious change here too.
_FREE_LADDER = [
    "nvidia/nemotron-3.5-super-vl:free",
    "minimax/minimax-m3.1-flash:free",
    "nvidia/nemotron-3.5-lightning:free",
    "dots-studio/dots-3-note-preview:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "google/gemma-4-31b-it:free",
]
_FREE_LAST_RESORT = "minimax/minimax-m3.1-flash:free"


def test_static_free_ladder_matches_curated_dev_tier():
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS
    from hedgefund_agents.llm.adapters.openrouter import (
        _EMPTY_CONTENT_FALLBACK,
        _RATE_LIMIT_FALLBACK,
        _free_last_resort_model,
        _static_free_ladder,
    )

    assert _static_free_ladder() == _FREE_LADDER == DEV_TIER_SLUGS
    assert _free_last_resort_model() == _FREE_LAST_RESORT
    # The pinned different-provider hops must themselves be live ladder rungs —
    # the pre-2026-10 maps pointed at delisted slugs and a hop onto them 404'd.
    for src, dst in {**_EMPTY_CONTENT_FALLBACK, **_RATE_LIMIT_FALLBACK}.items():
        assert src in _FREE_LADDER and dst in _FREE_LADDER and src != dst


def test_thinking_disabled_for_hybrid_routes_only():
    """Hybrid-thinking :free routes (nemotron-3.5*, dots-3) reason away the whole
    budget and return EMPTY content unless thinking is switched off (probed
    2026-10-20); non-listed routes must not get the extra param."""
    ok = {
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    http = _fake_http(ok)
    _build_client(http).complete(
        model="nvidia/nemotron-3.5-super-vl:free", messages=[Message("user", "hi")]
    )
    assert http.post.call_args.kwargs["json"]["reasoning"] == {"enabled": False}
    http = _fake_http(ok)
    _build_client(http).complete(
        model="dots-studio/dots-3-note-preview:free", messages=[Message("user", "hi")]
    )
    assert http.post.call_args.kwargs["json"]["reasoning"] == {"enabled": False}
    http = _fake_http(ok)
    _build_client(http).complete(
        model="minimax/minimax-m3.1-flash:free", messages=[Message("user", "hi")]
    )
    assert "reasoning" not in http.post.call_args.kwargs["json"]


def test_empty_content_falls_back_to_same_tier_model():
    """When a free slug returns empty content even after the caller's budget
    escalation, fall back ONCE to a same-price-tier (also free) model on a
    DIFFERENT provider rather than failing the run."""
    empty = _resp(
        {
            "choices": [{"message": {"content": None}, "finish_reason": ""}],
            "usage": {"completion_tokens": 91},
        }
    )
    good = _resp(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
    )
    http = _seq_http(empty, good)
    resp = _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    assert resp.text == '{"ok":true}'
    assert resp.model == _FREE_LADDER[1]
    assert http.post.call_count == 2
    assert http.post.call_args_list[1].kwargs["json"]["model"] == _FREE_LADDER[1]


def test_empty_content_free_chain_walks_ladder_bounded_and_never_paid(settings):
    """If every free rung is ALSO empty, the chain walks the :free ladder — each
    model tried at most once, bounded by LLM_MAX_MODEL_FALLBACKS — and stops
    WITHOUT ever touching the PAID last resort (price-class discipline). The
    same chain started from a PAID route does take that final rung."""
    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.LLM_LAST_RESORT_MODEL = _LAST_RESORT
    settings.LLM_FREE_ONLY = False
    empty = lambda: _resp(  # noqa: E731 — terse fixture
        {
            "choices": [{"message": {"content": None}, "finish_reason": ""}],
            "usage": {"completion_tokens": 91},
        }
    )
    http = _seq_http(*[empty() for _ in range(6)])
    resp = _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    assert resp.text == ""
    # configured + 5 fallbacks = 6 posts, each a DISTINCT :free rung, no 7th.
    assert http.post.call_count == 6
    models = [c.kwargs["json"]["model"] for c in http.post.call_args_list]
    assert len(set(models)) == 6
    assert all(m.endswith(":free") for m in models)
    assert set(models) <= set(_FREE_LADDER)
    assert _LAST_RESORT not in models  # no free → paid escalation

    # A PAID origin is already in the last resort's price class, so it makes the
    # one bounded hop, finds that empty too, and stops. No loop either way.
    http = _seq_http(empty(), empty())
    resp = _build_client(http).complete(
        model="openai/gpt-oss-120b", messages=[Message("user", "hi")]
    )
    assert resp.text == ""
    assert http.post.call_count == 2
    assert http.post.call_args_list[-1].kwargs["json"]["model"] == _LAST_RESORT


def test_rate_limit_fallback_on_exhausted_429(monkeypatch):
    """A :free route that stays 429 past the retry budget falls back once to a
    same-tier free model on a different provider (run 152 regression)."""
    monkeypatch.setattr("hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda _: None)
    # 6 × 429 (initial + MAX_RETRIES) exhausts _post_with_retry, then the
    # fallback model answers cleanly.
    rate_limited = [_resp({}, status_code=429, text="rate-limited") for _ in range(6)]
    ok = _resp({"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]})
    http = _seq_http(*rate_limited, ok)
    resp = _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    assert resp.text == '{"ok":true}'
    assert resp.model == _FREE_LADDER[1]
    assert http.post.call_args_list[-1].kwargs["json"]["model"] == _FREE_LADDER[1]


def test_rate_limit_free_chain_is_bounded_and_raises_rate_limited(monkeypatch, settings):
    """If EVERY free rung stays 429, the chain walks the whole :free ladder
    (each model tried at most once, guarded by _tried, bounded by
    LLM_MAX_MODEL_FALLBACKS) and the terminal 429 surfaces as the typed
    RateLimited (default: paid fallback off), not a raw HTTPStatusError."""
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.OPENROUTER_PAID_FALLBACK = False
    monkeypatch.setattr("hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda _: None)
    rate_limited = [_resp({}, status_code=429, text="rate-limited") for _ in range(36)]
    http = _seq_http(*rate_limited)
    with pytest.raises(RateLimited) as exc_info:
        _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    # 6 attempts × (configured + 5 fallbacks) = 36, no 37th.
    assert http.post.call_count == 36
    models = [c.kwargs["json"]["model"] for c in http.post.call_args_list]
    assert set(models) == set(_FREE_LADDER)
    assert exc_info.value.model in _FREE_LADDER
    assert _LAST_RESORT not in models


def test_empty_content_then_rate_limit_chains_to_third_model(monkeypatch):
    """The run-152 chain shape: rung 1 empty → rung 2 429 → rung 3 answers. Each
    model is tried at most once; the two fallback reasons compose via _tried."""
    monkeypatch.setattr("hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda _: None)
    empty = _resp(
        {
            "choices": [{"message": {"content": None}, "finish_reason": ""}],
            "usage": {"completion_tokens": 91},
        }
    )
    rate_limited = [_resp({}, status_code=429, text="rate-limited") for _ in range(6)]
    ok = _resp({"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]})
    http = _seq_http(empty, *rate_limited, ok)
    resp = _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    assert resp.text == '{"ok":true}'
    models = [c.kwargs["json"]["model"] for c in http.post.call_args_list]
    assert models[0] == _FREE_LADDER[0]
    assert models[1] == _FREE_LADDER[1]
    assert models[-1] == _FREE_LADDER[2]
    assert resp.model == _FREE_LADDER[2]
    assert len(set(models)) == 3


def test_empty_content_with_response_format_retries_without_it():
    """Parasail-style bug (run 155): a 200 with finish_reason='tool_calls',
    content=None and NO tool_calls drops the output entirely. The adapter drops
    response_format and retries once; the retry omits the field and recovers."""
    degenerate = _resp(
        {
            "choices": [{"message": {"content": None}, "finish_reason": "tool_calls"}],
            "usage": {"completion_tokens": 174},
            "provider": "Parasail",
        }
    )
    ok = _resp(
        {
            "choices": [{"message": {"content": '{"signal":"hold"}'}, "finish_reason": "stop"}],
        }
    )
    http = _seq_http(degenerate, ok)
    client = _build_client(http)
    resp = client.complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
        json_mode=True,
    )
    assert resp.text == '{"signal":"hold"}'
    assert http.post.call_count == 2
    # First attempt carried response_format; the retry dropped it.
    assert "response_format" in http.post.call_args_list[0].kwargs["json"]
    assert "response_format" not in http.post.call_args_list[1].kwargs["json"]
    # Memoized so the rest of the run skips the doomed format.
    assert "meta-llama/llama-3.3-70b-instruct" in client._no_response_format


def test_empty_content_drop_response_format_does_not_loop():
    """If the route stays empty even WITHOUT response_format, the drop-rf retry
    fires exactly once (guarded by _no_response_format) — no recursion storm."""
    degenerate = _resp(
        {
            "choices": [{"message": {"content": None}, "finish_reason": "tool_calls"}],
            "usage": {"completion_tokens": 174},
            "provider": "Parasail",
        }
    )
    still_empty = _resp(
        {
            "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 0},
        }
    )
    http = _seq_http(degenerate, still_empty)
    resp = _build_client(http).complete(
        model="meta-llama/llama-3.3-70b-instruct",
        messages=[Message("user", "hi")],
        json_mode=True,
    )
    assert resp.text == ""
    assert http.post.call_count == 2  # original + one drop-rf retry, no loop


def test_fallback_retry_second_failure_surfaces_and_does_not_loop():
    """If dropping response_format and retrying STILL fails with a non-retriable
    4xx, the error surfaces as HTTPStatusError and the fallback fires exactly
    once — it's a single `if`, not a loop, so there's no retry-of-retry storm."""
    rejection = _resp({}, status_code=405, text=_RF_REJECTION_TEXT)
    second_failure = _resp({}, status_code=400, text="bad request")
    http = _seq_http(rejection, second_failure)
    with pytest.raises(httpx.HTTPStatusError):
        _build_client(http).complete(
            model="meta-llama/llama-3.3-70b-instruct",
            messages=[Message("user", "hi")],
            json_mode=True,
        )
    assert http.post.call_count == 2  # original + one fallback retry, no loop


def test_terminal_429_raises_rate_limited_not_http_error(monkeypatch, settings):
    """A 429 whose retry budget AND same-tier free fallbacks are exhausted raises
    the typed RateLimited (so the run aborts once with an actionable message),
    not a raw httpx.HTTPStatusError. With the fallback bound at 0 there is no
    onward hop, so the terminal branch is reached after the route's own retries."""
    settings.LLM_MAX_MODEL_FALLBACKS = 0
    monkeypatch.setattr("hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda _: None)
    rate_limited = [_resp({}, status_code=429, text="upstream pool saturated") for _ in range(6)]
    http = _seq_http(*rate_limited)
    with pytest.raises(RateLimited) as exc_info:
        _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    assert exc_info.value.model == _FREE_LADDER[0]
    assert exc_info.value.status_code == 429
    assert "upstream pool saturated" in exc_info.value.body
    assert http.post.call_count == 6  # retries spent, no fallback allowed


def test_retry_after_is_clamped(monkeypatch, settings):
    """A large upstream Retry-After is clamped to RETRY_AFTER_CAP_SECONDS so a
    saturated pool can't stall the worker for minutes per call."""
    from hedgefund_agents.llm.adapters.openrouter import RETRY_AFTER_CAP_SECONDS

    settings.LLM_MAX_MODEL_FALLBACKS = 0
    waits: list[float] = []
    monkeypatch.setattr(
        "hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda w: waits.append(w)
    )

    def _rl():
        r = _resp({}, status_code=429, text="rate-limited")
        r.headers = {"retry-after": "29"}  # 29s hint, far above the cap
        return r

    http = _seq_http(*[_rl() for _ in range(6)])
    with pytest.raises(RateLimited):
        _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    assert waits, "expected at least one retry sleep"
    # Each clamped sleep is at most the cap + the <=0.5s jitter, never the 29s hint.
    assert max(waits) <= RETRY_AFTER_CAP_SECONDS + 0.5


def test_paid_fallback_off_by_default_does_not_escalate(monkeypatch, settings):
    """With OPENROUTER_PAID_FALLBACK off (the default), an exhausted free chain
    raises RateLimited rather than silently escalating to a paid route."""
    settings.OPENROUTER_PAID_FALLBACK = False
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    monkeypatch.setattr("hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda _: None)
    rate_limited = [_resp({}, status_code=429, text="rate-limited") for _ in range(36)]
    http = _seq_http(*rate_limited)
    with pytest.raises(RateLimited):
        _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    # 6 free rungs × 6 attempts -> no paid hop. 36 posts, never a 37th.
    assert http.post.call_count == 36
    assert _LAST_RESORT not in [c.kwargs["json"]["model"] for c in http.post.call_args_list]


def test_paid_fallback_on_escalates_to_paid_route_once(monkeypatch, settings):
    """With OPENROUTER_PAID_FALLBACK on, an exhausted free chain hops ONCE to the
    paid (non-:free) analytical default instead of failing the run. _tried guards
    against re-hopping, so it's exactly one extra call."""
    settings.OPENROUTER_PAID_FALLBACK = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    monkeypatch.setattr("hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda _: None)
    rate_limited = [_resp({}, status_code=429, text="rate-limited") for _ in range(36)]
    ok = _resp({"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]})
    http = _seq_http(*rate_limited, ok)
    resp = _build_client(http).complete(model=_FREE_LADDER[0], messages=[Message("user", "hi")])
    assert resp.text == '{"ok":true}'
    # 6 free rungs × 6×429 -> paid llama-3.3-70b (ok) = 37 posts.
    assert resp.model == "meta-llama/llama-3.3-70b-instruct"
    assert http.post.call_count == 37
    assert http.post.call_args_list[-1].kwargs["json"]["model"] == (
        "meta-llama/llama-3.3-70b-instruct"
    )


# --- L2 self-healing: last-resort fallback to a known-good non-reasoning model ---
# Regression coverage for the three production bugs that used to kill a whole run
# because one of ~16 agents drew a bad route:
#   Bug A (404, runs 228-235), Bug B (empty/reasoning-exhaustion, 236/237),
#   Bug C (non-JSON 2xx body, run 241).


@pytest.fixture
def _heal(settings):
    """Deterministic self-heal config regardless of the ambient test settings."""
    settings.LLM_SELF_HEAL = True
    settings.LLM_LAST_RESORT_MODEL = _LAST_RESORT
    settings.LLM_FREE_ONLY = False
    return settings


def test_self_heal_model_unavailable_hops_to_last_resort(_heal):
    """Bug A: a 404/dead route used to raise ModelUnavailable and fail the whole
    run. Self-heal hops ONCE to the known-good non-reasoning last resort. The
    origin is a PAID slug: the last resort is paid, and price-class discipline
    keeps it off the end of a `:free` chain."""
    dead = _resp({}, status_code=404, text="No endpoints found")
    good = _resp(
        {
            "choices": [{"message": {"content": '{"signal":"buy"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    http = _seq_http(dead, good)
    resp = _build_client(http).complete(
        model="arcee-ai/trinity-large-thinking", messages=[Message("user", "hi")]
    )
    assert resp.text == '{"signal":"buy"}'
    assert resp.model == _LAST_RESORT
    assert http.post.call_count == 2
    assert http.post.call_args_list[1].kwargs["json"]["model"] == _LAST_RESORT


def test_self_heal_free_route_never_hops_to_the_paid_last_resort(_heal):
    """The cost-leak fix, kept: a dead `:free` route walks only :free rungs — the
    PAID last resort is out of its price class — so when the whole free ladder
    is dead too, ModelUnavailable surfaces without ever billing the account.
    (Run 1178 regression: pre-fix a :free route with no live DB siblings had NO
    rung at all and died on the first 404; now the static ladder catches it.)"""
    _heal.LLM_MAX_MODEL_FALLBACKS = 5
    http = _seq_http(*[_resp({}, status_code=404, text="No endpoints found") for _ in range(6)])
    with pytest.raises(ModelUnavailable):
        _build_client(http).complete(
            model="arcee-ai/trinity-large-thinking:free", messages=[Message("user", "hi")]
        )
    models = [c.kwargs["json"]["model"] for c in http.post.call_args_list]
    assert http.post.call_count == 6  # configured + 5 free fallbacks, no 7th
    assert models[1:] == _FREE_LADDER[:5]
    assert all(m.endswith(":free") for m in models)
    assert _LAST_RESORT not in models


def test_self_heal_dead_free_route_recovers_on_static_free_ladder(_heal):
    """Run 1178 shape: a dead :free pick (404 while still listed upstream) with
    nothing in its DB tier menu hops onto the static :free ladder and the agent
    still gets a real answer — instead of the whole run ending FAILED."""
    dead = _resp(
        {}, status_code=404, text='{"error":{"code":404,"message":"Provider returned error"}}'
    )
    good = _resp(
        {
            "choices": [{"message": {"content": '{"signal":"buy"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    http = _seq_http(dead, good)
    resp = _build_client(http).complete(
        model="nvidia/nemotron-3-super-120b-a12b:free", messages=[Message("user", "hi")]
    )
    assert resp.text == '{"signal":"buy"}'
    assert resp.model == _FREE_LADDER[0]
    assert http.post.call_count == 2
    assert resp.cost_usd == 0.0  # :free rung priced by contract even with no catalog row


def test_self_heal_model_unavailable_both_dead_raises_no_loop(_heal):
    """If the last resort is ALSO unavailable (genuinely broken account), the hop
    fires exactly once (guarded by _tried) and ModelUnavailable surfaces."""
    dead = _resp({}, status_code=404, text="No endpoints found")
    http = _seq_http(dead, _resp({}, status_code=404, text="No endpoints found"))
    with pytest.raises(ModelUnavailable):
        _build_client(http).complete(model="minimax/minimax-m2.5", messages=[Message("user", "hi")])
    assert http.post.call_count == 2  # original + one last-resort hop, no loop


def test_self_heal_off_preserves_raise(_heal):
    """With LLM_SELF_HEAL off, a 404 raises ModelUnavailable immediately (old
    behavior) — no hop."""
    _heal.LLM_SELF_HEAL = False
    http = _seq_http(_resp({}, status_code=404, text="No endpoints found"))
    with pytest.raises(ModelUnavailable):
        _build_client(http).complete(
            model="arcee-ai/trinity-large-thinking:free", messages=[Message("user", "hi")]
        )
    assert http.post.call_count == 1


def test_non_json_body_retries_same_model_and_recovers(_heal):
    """Bug C (run 241): a 2xx whose body is not JSON is a transient provider
    hiccup; re-POST the SAME model and recover without changing models."""
    good = _resp(
        {
            "choices": [{"message": {"content": '{"signal":"hold"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    http = _seq_http(_nonjson_resp(), good)
    resp = _build_client(http).complete(model=_LAST_RESORT, messages=[Message("user", "hi")])
    assert resp.text == '{"signal":"hold"}'
    assert resp.model == _LAST_RESORT  # recovered on the same model, no hop
    assert http.post.call_count == 2


def test_non_json_body_persists_then_hops_to_last_resort(_heal):
    """If a non-JSON body persists across the re-POST budget on a NON-last-resort
    model, self-heal hops to the last resort rather than crashing the run."""
    # _json_with_retry: original + 2 re-POSTs = 3 non-JSON, then the hop succeeds.
    nonjson = [_nonjson_resp() for _ in range(3)]
    good = _resp(
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
    )
    http = _seq_http(*nonjson, good)
    resp = _build_client(http).complete(model="some/flaky-model", messages=[Message("user", "hi")])
    assert resp.text == '{"ok":true}'
    assert resp.model == _LAST_RESORT
    assert http.post.call_count == 4  # 3 non-JSON on flaky + 1 last-resort


def test_self_heal_on_transport_timeout_hops_to_last_resort(_heal, monkeypatch):
    """Bug B / run 236 class: a route that stalls at the transport layer trips
    the tightened read timeout (L1) as an httpx.ReadTimeout; after the retry
    budget is spent the adapter hops to the non-reasoning last resort (L2)
    instead of letting the timeout/hang reach the run."""
    monkeypatch.setattr("hedgefund_agents.llm.adapters.openrouter.time.sleep", lambda _: None)
    good = _resp(
        {
            "choices": [{"message": {"content": '{"signal":"buy"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    http = MagicMock()
    # 6 transport timeouts (initial + MAX_RETRIES) exhaust _post_with_retry for the
    # stalled model, then the last-resort model answers cleanly.
    http.post.side_effect = [httpx.ReadTimeout("stalled") for _ in range(6)] + [good]
    resp = _build_client(http).complete(model="qwen/qwen3.6-27b", messages=[Message("user", "hi")])
    assert resp.text == '{"signal":"buy"}'
    assert resp.model == _LAST_RESORT
    assert http.post.call_args_list[-1].kwargs["json"]["model"] == _LAST_RESORT


def test_self_heal_terminal_empty_content_hops_to_last_resort(_heal):
    """Bug B (runs 236/237): a reasoning slug with no _EMPTY_CONTENT_FALLBACK entry
    returns empty content; self-heal hops to the non-reasoning last resort so the
    agent still produces a real signal instead of an empty one."""
    empty = _resp(
        {
            "choices": [{"message": {"content": None}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 2048},
        }
    )
    good = _resp(
        {
            "choices": [{"message": {"content": '{"signal":"sell"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    http = _seq_http(empty, good)
    resp = _build_client(http).complete(model="qwen/qwen3.6-27b", messages=[Message("user", "hi")])
    assert resp.text == '{"signal":"sell"}'
    assert resp.model == _LAST_RESORT
    assert http.post.call_count == 2
