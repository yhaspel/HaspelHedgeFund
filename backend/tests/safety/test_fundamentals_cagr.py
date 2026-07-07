"""P5-SH WS1.4 — fundamentals anti-hallucination property test.

revenue_cagr_3y is arithmetic, not judgment. run_fundamentals now computes it
deterministically from the revenue series and overrides whatever the LLM emits.
This seeds a known geometric revenue series, hand-computes the annualized CAGR,
and asserts the emitted figure lands within 1% of it — regardless of the (wrong)
value the stub LLM returns.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest

import hedgefund_agents.analytical.fundamentals as fmod
from apps.data.interfaces import FundamentalRow
from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.outputs import FundamentalsOutput

AS_OF = dt.date(2024, 12, 31)
# Eight quarter-ends, oldest first — a 2-year window of quarterly revenue.
QUARTER_ENDS = [
    dt.date(2023, 3, 31), dt.date(2023, 6, 30), dt.date(2023, 9, 30),
    dt.date(2023, 12, 31), dt.date(2024, 3, 31), dt.date(2024, 6, 30),
    dt.date(2024, 9, 30), dt.date(2024, 12, 31),
]


class _RevenueProvider:
    name = "stub"

    def __init__(self, values):
        self._values = values  # oldest-first, aligned to QUARTER_ENDS

    def get_fundamentals(self, ticker, metrics, *, as_of, lookback_quarters=8):
        return [
            FundamentalRow(ticker=ticker, as_of_date=AS_OF, period_end=pe,
                           metric="revenue", value=Decimal(str(v)))
            for pe, v in zip(QUARTER_ENDS, self._values, strict=True)
        ]


class _WrongCagrLLM:
    """call_structured stand-in returning a deliberately wrong revenue_cagr_3y."""

    def __init__(self, wrong: float):
        self._wrong = wrong

    def __call__(self, client, *, model, schema, messages, max_tokens, cache_ctx, **_kw):
        parsed = FundamentalsOutput(
            revenue_cagr_3y=self._wrong, gross_margin=0.4, operating_margin=0.3,
            fcf_margin=0.2, roic=0.15, debt_to_equity=0.5, quality_score=80, notes="x",
        )
        resp = LLMResponse(text="{}", model=model, provider="fake", cost_usd=0.0)
        return parsed, resp


def _run(provider, llm):
    with patch.object(fmod, "call_structured", llm), \
         patch.object(fmod, "get_llm", return_value=object()), \
         patch.object(fmod, "record_llm_call"), \
         patch("apps.backtests.cache.make_cache_ctx", return_value=None):
        return fmod.run_fundamentals({
            "ticker": "AAPL", "as_of_date": AS_OF,
            "data_provider": provider, "ownership_provider": None,
        })


@pytest.mark.parametrize("r", [1.05, 1.10, 1.0, 0.95])
def test_revenue_cagr_overrides_llm_estimate(r):
    values = [100.0 * (r ** i) for i in range(len(QUARTER_ENDS))]
    # Annualized CAGR of a geometric quarterly series is exactly r**4 - 1.
    expected = r ** 4 - 1.0
    out = _run(_RevenueProvider(values), _WrongCagrLLM(wrong=99.0))["fundamentals"]
    emitted = out["revenue_cagr_3y"]
    assert emitted != 99.0, "the LLM's estimate was not overridden"
    if expected == 0.0:
        assert abs(emitted) <= 0.01
    else:
        assert abs(emitted - expected) / abs(expected) <= 0.01


def test_falls_back_to_llm_when_series_non_computable():
    class _OneRow:
        name = "stub"

        def get_fundamentals(self, ticker, metrics, *, as_of, lookback_quarters=8):
            return [FundamentalRow(ticker=ticker, as_of_date=AS_OF,
                                   period_end=AS_OF, metric="revenue",
                                   value=Decimal("100"))]

    out = _run(_OneRow(), _WrongCagrLLM(wrong=0.42))["fundamentals"]
    # Only one point → cannot compute → the model's number stands unchanged.
    assert out["revenue_cagr_3y"] == pytest.approx(0.42)
