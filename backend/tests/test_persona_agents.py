"""Persona agents: mock LLM, assert valid PersonaOutput written to state."""
from __future__ import annotations

import datetime as dt
import json
from unittest.mock import patch

import pytest

from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.personas import PERSONA_NODES


class _FakeLLM:
    provider = "fake"

    def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
        payload = {
            "signal": "bullish",
            "confidence": 75,
            "thesis": "Strong moat and pricing power; durable cash generation supports buy.",
            "key_risks": ["regulatory", "execution"],
            "intrinsic_value_estimate": 200.0,
            "margin_of_safety_pct": 25.0,
        }
        return LLMResponse(
            text=json.dumps(payload),
            model=model, provider=self.provider,
            prompt_tokens=10, completion_tokens=5, cost_usd=0.0,
        )


class _FakeFilingsProvider:
    name = "fake"

    def get_recent_filings(self, ticker, *, as_of, form_types, limit=4):
        return []


class _FakeOwnershipProvider:
    name = "fake"

    def get_issuer_ownership(self, ticker, *, as_of):
        return None

    def get_filer_portfolio(self, filer_cik, *, as_of):
        return None


@pytest.mark.django_db
@pytest.mark.parametrize("persona_name", list(PERSONA_NODES.keys()))
def test_persona_produces_valid_output(persona_name: str) -> None:
    state = {
        "ticker": "AAPL",
        "as_of_date": dt.date(2024, 12, 31),
        "filings_provider": _FakeFilingsProvider(),
        "ownership_provider": _FakeOwnershipProvider(),
        "fundamentals": {"quality_score": 80},
        "technicals": {"signal": "bullish"},
        "valuation": {"current_price": 150.0},
        "sentiment": {"score": 0.0},
    }
    with patch("hedgefund_agents.personas._base.get_llm", return_value=_FakeLLM()), \
         patch("hedgefund_agents.personas._base.record_llm_call"):
        result = PERSONA_NODES[persona_name](state)
    out = result[persona_name]
    assert out["signal"] in {"bullish", "neutral", "bearish"}
    assert 0 <= out["confidence"] <= 100
    assert len(out["thesis"]) > 20
