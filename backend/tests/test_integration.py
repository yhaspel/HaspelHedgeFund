"""End-to-end integration test for the Phase 1 single-persona graph.

Uses pytest-vcr cassettes so CI never hits live APIs:

  - First run with `VCR_RECORD_MODE=new_episodes` (and real keys in env)
    records all FMP / EDGAR / OpenRouter / Anthropic HTTP traffic to
    `tests/cassettes/test_integration/`.
  - Subsequent runs replay cassettes; no network, no API spend.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.data.providers.factory import get_edgar_provider, get_fmp_provider
from hedgefund_agents.graphs.single_persona import build_graph
from hedgefund_agents.models import LLMCall


def _run(model_overrides: dict[str, str]) -> dict:
    graph = build_graph()
    state = {
        "ticker": "AAPL",
        "as_of_date": dt.date(2024, 12, 31),
        "data_provider": get_fmp_provider(force_platform=True),
        "filings_provider": get_edgar_provider(),
        "ownership_provider": None,
        "model_overrides": model_overrides,
    }
    return graph.invoke(state)


@pytest.mark.django_db
@pytest.mark.vcr
def test_full_run_aapl_qwen() -> None:
    """OpenRouter path: Qwen3.6 27B for every agent, FMP fundamentals, EDGAR filings.

    Pinned via overrides so the cassette stays valid regardless of the global
    default (flipped to Haiku 4.5 on 2026-05-17).
    """
    qwen = "openrouter:qwen/qwen3.6-27b"
    out = _run(model_overrides={
        a: qwen for a in (
            "fundamentals", "technicals", "valuation", "sentiment", "buffett"
        )
    })

    decision = out["decision"]
    assert decision["ticker"] == "AAPL"
    assert decision["action"] in {"buy", "hold", "sell"}
    assert 0 <= decision["confidence"] <= 100

    buffett = out["buffett"]
    assert buffett["signal"] in {"bullish", "neutral", "bearish"}
    assert isinstance(buffett["thesis"], str) and len(buffett["thesis"]) > 50

    # Every agent recorded an LLMCall with cost.
    calls = list(LLMCall.objects.all())
    agents = {c.agent_name for c in calls}
    assert {"fundamentals", "technicals", "buffett"} <= agents
    assert all(c.cost_usd >= Decimal("0") for c in calls)


@pytest.mark.django_db
@pytest.mark.vcr
def test_full_run_aapl_sonnet() -> None:
    """Same code, different model: Sonnet 4.6 via Anthropic for Buffett."""
    out = _run(
        model_overrides={
            "fundamentals": "anthropic:claude-haiku-4-5-20251001",
            "technicals": "anthropic:claude-haiku-4-5-20251001",
            "buffett": "anthropic:claude-sonnet-4-6",
        }
    )
    assert out["decision"]["ticker"] == "AAPL"
    assert out["buffett"]["signal"] in {"bullish", "neutral", "bearish"}
    providers = {c.provider for c in LLMCall.objects.all()}
    assert "anthropic" in providers
