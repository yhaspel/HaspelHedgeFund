"""OllamaClient adapter unit tests — the response_format fallback.

Mirrors test_openrouter_adapter.py. An OpenAI-compatible local backend (vLLM,
llama.cpp, LM Studio) reached via ollama_host may reject
response_format=json_object; the adapter drops the hint and retries once,
memoizing the model. (Stock Ollama supports json_object, so this path only
fires for third-party OpenAI-compatible endpoints.)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from hedgefund_agents.llm.adapters.ollama import OllamaClient
from hedgefund_agents.llm.client import Message


def _resp(payload: dict, status_code: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = text
    resp.request = MagicMock()
    return resp


def _seq_http(*responses: MagicMock) -> MagicMock:
    """HTTP whose successive .post() calls return the given responses in order."""
    http = MagicMock()
    http.post.side_effect = list(responses)
    return http


def _ok(content: str = '{"ok":true}') -> MagicMock:
    return _resp({
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    })


# A third-party backend phrases the rejection differently from DeepInfra; the
# shared detector matches on the response_format mention + rejection phrase.
_RF_REJECTION_TEXT = (
    '{"error":{"message":"response_format json_object is not supported by this '
    'model","type":"invalid_request_error","param":"response_format"}}'
)


def _build_client(http) -> OllamaClient:
    return OllamaClient(host="http://x:11434", http=http)


def test_retries_without_response_format_when_backend_rejects_json_object():
    """The rejecting backend returns a 4xx; the adapter must drop response_format
    and retry once rather than surfacing a fatal HTTPStatusError."""
    rejection = _resp({}, status_code=400, text=_RF_REJECTION_TEXT)
    http = _seq_http(rejection, _ok('{"signal":"buy"}'))
    resp = _build_client(http).complete(
        model="llama3.3:8b",
        messages=[Message("user", "hi")],
        json_mode=True,
    )
    assert resp.text == '{"signal":"buy"}'
    assert resp.provider == "ollama"
    assert resp.cost_usd == 0.0
    assert http.post.call_count == 2
    # First attempt carried response_format; the retry dropped it.
    assert http.post.call_args_list[0].kwargs["json"]["response_format"] == {"type": "json_object"}
    assert "response_format" not in http.post.call_args_list[1].kwargs["json"]


def test_memoizes_unsupported_model_and_skips_response_format_next_call():
    """After a model is learned to reject response_format, subsequent calls on the
    same (lru_cache'd) client skip it proactively — no repeated 4xx + retry."""
    http = _seq_http(_resp({}, status_code=400, text=_RF_REJECTION_TEXT), _ok(), _ok())
    client = _build_client(http)
    kwargs = {"model": "llama3.3:8b", "messages": [Message("user", "hi")], "json_mode": True}
    client.complete(**kwargs)  # learns: rejection then retry
    client.complete(**kwargs)  # memoized: single request, no response_format
    assert http.post.call_count == 3
    assert "response_format" not in http.post.call_args_list[2].kwargs["json"]


def test_unrelated_4xx_still_raises_and_does_not_retry():
    """A 4xx that is NOT about response_format keeps current behavior: surface as
    HTTPStatusError, no silent retry-without-hint."""
    http = _seq_http(_resp({}, status_code=400, text="bad request"))
    with pytest.raises(httpx.HTTPStatusError):
        _build_client(http).complete(
            model="llama3.3:8b",
            messages=[Message("user", "hi")],
            json_mode=True,
        )
    assert http.post.call_count == 1


def test_fallback_retry_second_failure_surfaces_and_does_not_loop():
    """If dropping response_format and retrying STILL fails, the error surfaces
    and the fallback fires exactly once (single `if`, not a loop)."""
    rejection = _resp({}, status_code=400, text=_RF_REJECTION_TEXT)
    second_failure = _resp({}, status_code=500, text="boom")
    http = _seq_http(rejection, second_failure)
    with pytest.raises(httpx.HTTPStatusError):
        _build_client(http).complete(
            model="llama3.3:8b",
            messages=[Message("user", "hi")],
            json_mode=True,
        )
    assert http.post.call_count == 2  # original + one fallback retry, no loop
