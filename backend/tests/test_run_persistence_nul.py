"""Regression for Bug D: an LLM output containing a NUL byte (U+0000) used to
crash AgentMessage/Decision persistence with PostgreSQL
`DataError: unsupported Unicode escape sequence` AFTER every LLM call had
already succeeded — failing the whole run at the very last step. The persistence
boundary now scrubs NUL bytes (apps.runs.tasks._scrub_nul). Surfaced by the
self-heal PROOF A run (248): once the dead-route hop let the council finish, the
news_digest payload's NUL hit the insert.
"""
from __future__ import annotations

import datetime as dt

import pytest

from apps.accounts.models import User
from apps.runs.models import AgentMessage, Decision, Run
from apps.runs.tasks import _persist_outputs, _scrub_nul

pytestmark = pytest.mark.django_db


def test_scrub_nul_removes_null_bytes_recursively():
    blob = {
        "digest": "ab\x00cd",
        "events": ["x\x00y", {"k": "z\x00"}],
        "count": 5,
        "clean": "fine",
    }
    out = _scrub_nul(blob)
    assert out == {
        "digest": "abcd",
        "events": ["xy", {"k": "z"}],
        "count": 5,
        "clean": "fine",
    }


def test_scrub_nul_is_identity_when_no_nul():
    blob = {"a": "plain", "b": [1, 2, {"c": "ok"}]}
    assert _scrub_nul(blob) == blob


def test_persist_outputs_survives_nul_in_agent_and_decision():
    user = User.objects.create_user(email="nul@test.x", password="supersecret")
    run = Run.objects.create(user=user, tickers=["JNJ"], as_of_date=dt.date(2026, 6, 3))
    state = {
        "news_digest": {
            "ticker": "JNJ", "digest": "bad\x00byte",
            "material_events": ["evt\x00"], "sentiment_score": 0.0,
        },
        "decision": {
            "ticker": "JNJ", "action": "buy", "aggregate_confidence": 70,
            "rationale": "solid\x00thesis", "dissenting_personas": [],
            "target_quantity": 0, "target_weight_pct": 0,
        },
        "risk": {"rationale": "ok\x00", "hard_caps_applied": []},
    }
    # Must not raise (previously: DataError on the NUL insert).
    _persist_outputs(run, state, [])

    msg = AgentMessage.objects.get(run=run, agent_name="news_digest")
    assert "\x00" not in msg.parsed_output["digest"]
    assert msg.parsed_output["digest"] == "badbyte"
    assert msg.parsed_output["material_events"] == ["evt"]

    dec = Decision.objects.get(run=run, ticker="JNJ")
    assert dec.rationale == "solidthesis"
    assert "\x00" not in dec.risk_overrides.get("rationale", "")
