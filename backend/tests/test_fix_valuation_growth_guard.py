"""Post-deploy finding (2026-09-09): the DCF leg exploded on hyper-growth names.

Production run #941 (NVDA, engine 490b94d): trailing 3-year revenue CAGR ~+90%/yr
compounded for ten years gave ``dcf_fair_value`` $17,438/share against a $231
price and ``upside_pct`` +2,451%. The old constant-zero rescale had hidden this.
Guard: cap + fade the stage-1 growth, take the median leg for the midpoint, and
flag a >10x band.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hedgefund_agents.analytical import valuation as val
from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.outputs import ValuationOutput

pytestmark = pytest.mark.django_db


class _EchoLLM:
    """Minimal stand-in: the node overwrites every number it returns."""

    provider = "fake"

    def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
        return LLMResponse(
            text=ValuationOutput(
                fair_value_low=0, fair_value_high=0, current_price=0,
                upside_pct=0, most_sensitive_assumption="x",
            ).model_dump_json(),
            model=model, provider="fake",
        )


def _rows(revenues, fcf, ni, equity, shares):
    rows = []
    d = dt.date(2023, 1, 1)
    for i, rev in enumerate(revenues):
        when = d + dt.timedelta(days=91 * i)
        rows.append(SimpleNamespace(metric="revenue", value=rev, period_end=when))
        rows.append(SimpleNamespace(metric="free_cash_flow", value=fcf, period_end=when))
        rows.append(SimpleNamespace(metric="net_income", value=ni, period_end=when))
        rows.append(SimpleNamespace(metric="total_equity", value=equity, period_end=when))
        rows.append(SimpleNamespace(metric="shares_outstanding", value=shares, period_end=when))
    return rows


def test_dcf_growth_is_capped_and_faded_not_compounded_for_ten_years():
    # +90%/yr trailing CAGR: uncapped ten-year compounding is ~6,000x.
    uncapped_like = val.compute_dcf(fcf_ttm=1.0, growth=0.25)  # the cap value itself
    runaway = val.compute_dcf(fcf_ttm=1.0, growth=0.90)
    assert runaway is not None and uncapped_like is not None
    # Capped: a 90% input must produce exactly what a 25% input produces.
    assert abs(runaway - uncapped_like) < 1e-9
    # And faded: it must sit below a flat 25%-for-ten-years DCF (same WACC,
    # terminal growth and horizon, terminal value included).
    wacc, tg, h = val.DCF_WACC_DEFAULT, val.DCF_TERMINAL_GROWTH, val.DCF_HORIZON_YEARS
    fcf, naive = 1.0, 0.0
    for t in range(1, h + 1):
        fcf *= 1.25
        naive += fcf / (1 + wacc) ** t
    naive += fcf * (1 + tg) / (wacc - tg) / (1 + wacc) ** h
    assert runaway < naive


def test_dcf_growth_floor_applies_to_collapsing_revenue():
    assert val.compute_dcf(fcf_ttm=1.0, growth=-0.60) == val.compute_dcf(
        fcf_ttm=1.0, growth=val.DCF_GROWTH_FLOOR
    )


def test_median_midpoint_survives_one_runaway_leg(monkeypatch):
    """A single runaway leg must not drag upside_pct with it.

    Legs: DCF 1000 (runaway), multiples 20, residual income 25, price 22.
      mean   = 348.33  -> upside +1483%   (the old behaviour)
      median = 25      -> upside +13.6%   (what we want)
    Drives the real node, so this fails if the median change is reverted.
    """
    monkeypatch.setattr(val, "compute_dcf", lambda *a, **k: 1000.0)
    monkeypatch.setattr(val, "compute_multiples", lambda *a, **k: 20.0)
    monkeypatch.setattr(val, "compute_residual_income", lambda *a, **k: 25.0)

    class _Data:
        def get_fundamentals(self, *a, **k):
            return _rows([100, 110, 120, 130], fcf=10, ni=8, equity=50, shares=1_000)

        def get_daily_bars(self, *a, **k):
            return [SimpleNamespace(close=22.0)]

    state = {"ticker": "X", "as_of_date": dt.date(2026, 9, 9), "data_provider": _Data()}
    with (
        patch("hedgefund_agents.analytical.valuation.get_llm", return_value=_EchoLLM()),
        patch("hedgefund_agents.analytical.valuation.record_llm_call"),
    ):
        out = val.run_valuation(state)["valuation"]

    # Median leg (25) against a 22 price, NOT the 348 mean.
    assert out["upside_pct"] == pytest.approx((25.0 - 22.0) / 22.0 * 100, rel=1e-6)
    assert out["fair_value_low"] == 20.0
    assert out["fair_value_high"] == 1000.0
    # 1000/20 = 50x disagreement, so the band is flagged low-confidence.
    assert "Wide band" in out["notes"]


def test_narrow_band_is_not_flagged(monkeypatch):
    """The >10x warning must not fire on ordinary agreement between methods."""
    monkeypatch.setattr(val, "compute_dcf", lambda *a, **k: 24.0)
    monkeypatch.setattr(val, "compute_multiples", lambda *a, **k: 20.0)
    monkeypatch.setattr(val, "compute_residual_income", lambda *a, **k: 25.0)

    class _Data:
        def get_fundamentals(self, *a, **k):
            return _rows([100, 110, 120, 130], fcf=10, ni=8, equity=50, shares=1_000)

        def get_daily_bars(self, *a, **k):
            return [SimpleNamespace(close=22.0)]

    state = {"ticker": "X", "as_of_date": dt.date(2026, 9, 9), "data_provider": _Data()}
    with (
        patch("hedgefund_agents.analytical.valuation.get_llm", return_value=_EchoLLM()),
        patch("hedgefund_agents.analytical.valuation.record_llm_call"),
    ):
        out = val.run_valuation(state)["valuation"]

    assert out["upside_pct"] == pytest.approx((24.0 - 22.0) / 22.0 * 100, rel=1e-6)
    assert "Wide band" not in (out["notes"] or "")
