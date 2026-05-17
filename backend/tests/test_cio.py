"""CIO discretionary layer: ratifies PM by default, can downsize/flip, toggle off."""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.outputs import CioOutput
from hedgefund_agents.portfolio.cio import run_cio

pytestmark = pytest.mark.django_db


_PM_TICKET = {
    "ticker": "AAPL",
    "action": "buy",
    "target_quantity": 100.0,
    "target_weight_pct": 8.0,
    "aggregate_confidence": 75,
    "rationale": "Aggregated persona signal: +0.40 (confidence 75). Action: buy.",
    "dissenting_personas": [],
}


def _fake_llm_returning(cio: CioOutput):
    class _Fake:
        provider = "fake"
        def complete(self, **kwargs):
            return LLMResponse(
                text=cio.model_dump_json(),
                model=kwargs.get("model", "fake"),
                provider="fake",
                prompt_tokens=10, completion_tokens=5, cost_usd=0.0, latency_ms=1,
            )
    return _Fake()


def _state(**extra) -> dict:
    base = {
        "ticker": "AAPL",
        "as_of_date": dt.date(2024, 12, 31),
        "decision": dict(_PM_TICKET),
        "risk": {"max_position_pct_for_this_trade": 0.10, "veto": False, "rationale": "ok"},
    }
    base.update(extra)
    return base


def test_cio_ratifies_pm_unchanged_by_default():
    cio = CioOutput(
        ticker="AAPL", action="buy", target_weight_pct=8.0, target_quantity=100.0,
        overrode_pm=False, outlook="ratify", confidence=70,
    )
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_fake_llm_returning(cio)), \
         patch("hedgefund_agents.portfolio.cio.record_llm_call"):
        out = run_cio(_state())
    assert out["decision"]["action"] == "buy"
    assert out["decision"]["target_weight_pct"] == 8.0
    assert out["cio"]["overrode_pm"] is False
    # PM ticket preserved verbatim for audit.
    assert out["pm_decision"] == _PM_TICKET


def test_cio_can_downsize():
    cio = CioOutput(
        ticker="AAPL", action="buy", target_weight_pct=4.0, target_quantity=50.0,
        overrode_pm=True, override_reason="material risk in news_digest warranted half-size",
        outlook="downsize", confidence=65,
    )
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_fake_llm_returning(cio)), \
         patch("hedgefund_agents.portfolio.cio.record_llm_call"):
        out = run_cio(_state())
    assert out["decision"]["target_weight_pct"] == 4.0
    assert out["cio"]["overrode_pm"] is True


def test_cio_can_flip_to_hold():
    cio = CioOutput(
        ticker="AAPL", action="hold", target_weight_pct=0.0, target_quantity=0.0,
        overrode_pm=True, override_reason="news_digest flagged material guidance cut",
        outlook="flip to hold", confidence=80,
    )
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_fake_llm_returning(cio)), \
         patch("hedgefund_agents.portfolio.cio.record_llm_call"):
        out = run_cio(_state())
    assert out["decision"]["action"] == "hold"
    assert out["decision"]["target_quantity"] == 0.0


def test_cio_disabled_returns_no_change():
    out = run_cio(_state(disable_cio=True))
    assert out == {}


def test_cio_falls_back_to_pm_on_llm_failure():
    class _Boom:
        provider = "fake"
        def complete(self, **kwargs):
            raise RuntimeError("simulated 5xx")
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_Boom()):
        out = run_cio(_state())
    # Final decision matches PM; CIO marked as not-an-override.
    assert out["decision"]["action"] == _PM_TICKET["action"]
    assert out["decision"]["target_weight_pct"] == _PM_TICKET["target_weight_pct"]
    assert out["cio"]["overrode_pm"] is False
