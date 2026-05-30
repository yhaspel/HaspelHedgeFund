"""OpenRouter adapter unit tests.

The adapter has two non-obvious paths that have bitten us:
- 401/402/403/404 → typed `ModelUnavailable` (separate suite)
- finish_reason='tool_calls' with empty content → extract JSON from
  tool_calls[0].function.arguments instead of treating as empty-content retry.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient
from hedgefund_agents.llm.client import Message


def _resp(payload: dict, status_code: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
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
    http = _fake_http({
        "choices": [{
            "message": {"role": "assistant", "content": '{"signal":"buy","confidence":80}'},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    })
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
    http = _fake_http({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "emit_json", "arguments": args_json},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    })
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
    http = _fake_http({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "function": {"name": "emit_json", "arguments": "   "},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 0},
    })
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
    http = _fake_http({
        "choices": [{
            "message": {"content": '{"ok":true}'},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
    })
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
    http = _fake_http({
        "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    })
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
    http = _fake_http({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": '{"signal":"sell"}',
                "tool_calls": [{
                    "function": {"name": "emit_json", "arguments": '{"signal":"buy"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    })
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
    success = _resp({
        "choices": [{
            "message": {"content": '{"signal":"buy"}'},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 80, "completion_tokens": 12},
    })
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
    success = _resp({
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
    })
    success2 = _resp({
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
    })
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


def test_reasoning_effort_set_for_reasoning_slugs_only():
    """gpt-oss (and other reasoning slugs) get `reasoning={"effort":"low"}` so
    the token budget goes to the visible answer instead of hidden thinking
    (run 151 returned empty content, completion_tokens=91). Non-reasoning
    routes like the prod Llama-3.3-70B analytical default must NOT get the
    param — some providers reject unknown fields with a 400."""
    http = _fake_http({
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })
    client = _build_client(http)
    client.complete(model="openai/gpt-oss-120b:free", messages=[Message("user", "hi")])
    assert http.post.call_args.kwargs["json"]["reasoning"] == {"effort": "low"}

    http2 = _fake_http({
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })
    _build_client(http2).complete(
        model="meta-llama/llama-3.3-70b-instruct", messages=[Message("user", "hi")]
    )
    assert "reasoning" not in http2.post.call_args.kwargs["json"]


def test_empty_content_falls_back_to_same_tier_model():
    """When a free reasoning slug returns empty content even after the caller's
    budget escalation, fall back ONCE to a same-price-tier (also free) model
    rather than failing the run."""
    empty = _resp({
        "choices": [{"message": {"content": None}, "finish_reason": ""}],
        "usage": {"completion_tokens": 91},
    })
    good = _resp({
        "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    })
    http = _seq_http(empty, good)
    resp = _build_client(http).complete(
        model="openai/gpt-oss-120b:free", messages=[Message("user", "hi")]
    )
    assert resp.text == '{"ok":true}'
    assert resp.model == "meta-llama/llama-3.3-70b-instruct:free"
    assert http.post.call_count == 2
    assert http.post.call_args_list[1].kwargs["json"]["model"] == (
        "meta-llama/llama-3.3-70b-instruct:free"
    )


def test_empty_content_fallback_does_not_loop_when_fallback_also_empty():
    """If the fallback model is ALSO empty, surface the empty response — the
    fallback fires exactly once (guarded by _fallback_from), no recursion."""
    empty = _resp({
        "choices": [{"message": {"content": None}, "finish_reason": ""}],
        "usage": {"completion_tokens": 91},
    })
    http = _seq_http(empty, _resp({
        "choices": [{"message": {"content": None}, "finish_reason": ""}],
        "usage": {"completion_tokens": 0},
    }))
    resp = _build_client(http).complete(
        model="openai/gpt-oss-120b:free", messages=[Message("user", "hi")]
    )
    assert resp.text == ""
    assert http.post.call_count == 2  # original + one fallback, no loop


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
