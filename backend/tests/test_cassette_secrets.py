"""Guardrail: VCR cassettes must never contain unscrubbed secrets.

This is the "raw provider fixture scrub" task from the P1 improvements plan.
Run it in CI so a contributor who re-records a cassette without the redaction
filter gets a loud failure instead of leaking a key into git history.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CASSETTES_DIR = Path(__file__).parent / "cassettes"

# Patterns that should never appear unredacted in a committed cassette.
# Anthropic keys: sk-ant-...; OpenRouter: sk-or-v1-...; FMP: 32-hex; Tiingo:
# 40-hex; FRED: 32-hex; generic Bearer-encoded JWT-like blobs.
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openrouter_key", re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?!ant-|or-)[A-Za-z0-9]{32,}")),
    # Query-string API keys (FMP / Tiingo / FRED) — these MUST be REDACTED.
    (
        "qs_apikey",
        re.compile(r"[?&](?:apikey|api_key|token)=(?!REDACTED\b)[A-Za-z0-9_\-]{16,}"),
    ),
    # Bearer headers and x-api-key values that escaped redaction.
    (
        "bearer_long",
        re.compile(r"[Bb]earer\s+(?!REDACTED\b)[A-Za-z0-9_\-]{20,}"),
    ),
    (
        "x_api_key_header",
        re.compile(r"(?im)^\s*x-api-key:\s*(?!REDACTED\b)\S{16,}"),
    ),
]

# Permit the EDGAR User-Agent contact email — SEC requires one and the
# Phase 0 .env.example tells contributors to put their own there. We allow
# the example placeholder but flag anything that looks like a real personal
# address other than that.
ALLOWED_EMAILS = {"example@example.com", "your-email@example.com"}
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


@pytest.mark.parametrize(
    "cassette", sorted(CASSETTES_DIR.glob("*.yaml")), ids=lambda p: p.name
)
def test_cassette_has_no_secrets(cassette: Path) -> None:
    text = cassette.read_text(encoding="utf-8", errors="replace")
    for label, pattern in FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{cassette.name}: matched forbidden {label!r} pattern at "
            f"offset {match.start() if match else -1}. Re-record with the "
            "VCR redaction filter enabled."
        )
    for email in EMAIL_RE.findall(text):
        assert email.lower() in ALLOWED_EMAILS, (
            f"{cassette.name}: contains email {email!r} not on the allow-list. "
            "Either redact it or add it to ALLOWED_EMAILS with justification."
        )
