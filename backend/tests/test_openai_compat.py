"""Unit tests for the shared OpenAI-dialect helpers (adapters/_openai_compat)."""
from __future__ import annotations

import pytest

from hedgefund_agents.llm.adapters._openai_compat import is_response_format_unsupported


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
