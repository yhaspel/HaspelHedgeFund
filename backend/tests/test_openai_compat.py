"""Unit tests for the shared OpenAI-dialect helpers (adapters/_openai_compat)."""
from __future__ import annotations

import pytest

from hedgefund_agents.llm.adapters._openai_compat import (
    is_response_format_unsupported,
    to_openai_messages,
)
from hedgefund_agents.llm.client import Message


@pytest.mark.parametrize(
    "text,expected",
    [
        # Real DeepInfra/OpenRouter rejection body for nvidia/nemotron-3-nano.
        (
            '{"error":{"message":"json_object response format is not supported '
            'for model: nvidia/Nemotron-3-Nano-30B-A3B","param":"response_format"}}',
            True,
        ),
        ("json_object response format is not supported for model: x", True),
        ("the response_format parameter is unsupported here", True),
        ("this route does not support response_format", True),
        # Both halves required: a mention alone or a rejection alone must NOT
        # trigger the drop-and-retry fallback.
        ("Invalid response format version", False),  # mentions RF, no rejection token
        ("model is not supported", False),  # rejection token, no RF mention
        ("Method Not Allowed", False),
        ("rate limit exceeded", False),
        ("", False),
    ],
)
def test_is_response_format_unsupported_detection(text, expected):
    """The detector's AND boundary: a body must mention response_format AND
    express rejection for the fallback to fire."""
    assert is_response_format_unsupported(text) is expected


def test_to_openai_messages_merges_double_system():
    """Run-198 case: call_structured's schema-hint system + the agent's own
    system prompt collapse into ONE system turn so strict upstreams (which only
    accept messages[0] as system, then require user/assistant alternation) don't
    400 with 'roles must alternate'."""
    out = to_openai_messages([
        Message("system", "schema hint"),
        Message("system", "agent prompt"),
        Message("user", "analyze"),
    ])
    assert out == [
        {"role": "system", "content": "schema hint\n\nagent prompt"},
        {"role": "user", "content": "analyze"},
    ]


def test_to_openai_messages_leaves_alternating_untouched():
    """Already-alternating turns (incl. the validation-retry shape) pass through
    one-for-one — coalescing only fires on consecutive same-role turns."""
    msgs = [
        Message("system", "s"),
        Message("user", "u1"),
        Message("assistant", "a1"),
        Message("user", "u2"),
    ]
    assert to_openai_messages(msgs) == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]


def test_to_openai_messages_collapses_a_run_of_same_role():
    """Three+ consecutive same-role turns fold into a single joined turn."""
    out = to_openai_messages([
        Message("user", "a"),
        Message("user", "b"),
        Message("user", "c"),
    ])
    assert out == [{"role": "user", "content": "a\n\nb\n\nc"}]


def test_to_openai_messages_empty_is_empty():
    assert to_openai_messages([]) == []
