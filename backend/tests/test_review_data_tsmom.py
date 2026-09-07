"""Adversarial review (reviewer: data) — TSMOM trend score adjustment consistency.

  * F-TSMOM-PRICE-ONLY: ``tsmom_score`` reads ``Bar.close`` (price-only) while
    the Markov classifier reads ``adjusted_close`` (total return) and the
    backtests credit dividend cash. For a ~4%-yield bond ETF a flat price
    year is "flat" here but +4% on a total-return basis — the posture the
    council is told about flips on the dividend alone.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from apps.data.interfaces import Bar
from hedgefund_agents.macro.tsmom import tsmom_score


class _Prov:
    """A bond ETF: flat price, 4% of total return delivered as distributions
    (adjusted_close back-adjusted below close by the accrued dividends)."""

    def get_daily_bars(self, ticker, start, end, *, as_of):
        out = []
        d = start
        n = 0
        while d <= end:
            if d.weekday() < 5:
                n += 1
            d += dt.timedelta(days=1)
        total = n
        d = start
        i = 0
        while d <= end:
            if d.weekday() < 5:
                close = Decimal("100")
                # total-return index rises 4%/yr linearly: adjusted_close is
                # scaled so that adj[-1] == close[-1] and adj[0] == close/1.04.
                adj = Decimal(str(round(100 / (1 + 0.04 * (1 - i / max(1, total - 1))), 6)))
                out.append(Bar(ticker=ticker, date=d, open=close, high=close, low=close,
                               close=close, adjusted_close=adj, volume=1))
                i += 1
            d += dt.timedelta(days=1)
        return out


def test_tsmom_reads_the_total_return_series():
    """FIXED: tsmom_score reads ``adjusted_close``, so distributions count —
    a flat-price 4%-yield bond ETF is "up", matching the Markov classifier and
    the backtest engine rather than contradicting them."""
    ts = tsmom_score("TLT", dt.date(2026, 9, 7), _Prov())
    assert ts["available"] is True
    assert ts["r12"] > 0.03
    assert ts["posture"] == "up"
    # Same bars, total-return field: +4% over 12 months -> "up".
    as_of = dt.date(2026, 9, 7)
    bars = _Prov().get_daily_bars(
        "TLT", as_of - dt.timedelta(days=420), as_of, as_of=as_of
    )
    adj = [float(b.adjusted_close) for b in bars]
    r12_total = adj[-1] / adj[-1 - 252] - 1.0
    assert r12_total > 0.03
    assert ts["r12"] == round(r12_total, 4)
