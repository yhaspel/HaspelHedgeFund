"""OpenRouter adapter unit tests.

The adapter has two non-obvious paths that have bitten us:
- 401/402/403/404 → typed `ModelUnavailable` (separate suite)
- finish_reason='tool_calls' with empty content → extract JSON from
  tool_calls[0].function.arguments instead of treating as empty-content retry.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient
from hedgefund_agents.llm.client import Message


def _fake_http(payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = ""
    resp.request = MagicMock()
    http = MagicMock()
    http.post.return_value = resp
    return http


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
