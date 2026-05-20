"""P2f — market-neutral construction + beta estimator tests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from apps.portfolios.beta import _daily_returns, _ols_beta, compute_beta
from apps.portfolios.construction import (
    Candidate,
    Constraints,
    construct_market_neutral,
)


def _c(t, side, action, conf=80, sector="Tech"):
    return Candidate(
        ticker=t, sector=sector, side=side, action=action, confidence=conf,
        quality_weight=1.0, veto_reason=None,
    )


# ---------- Construction ----------

def test_neutral_balanced_betas_yields_zero_net_and_zero_beta():
    longs = [_c(f"L{i}", "long", "buy", conf=80, sector=f"S{i%3}") for i in range(4)]
    shorts = [_c(f"S{i}", "short", "open_short", conf=80, sector=f"S{i%3}") for i in range(4)]
    betas = {**{f"L{i}": 1.0 for i in range(4)}, **{f"S{i}": 1.0 for i in range(4)}}
    out = construct_market_neutral(
        longs + shorts,
        Constraints(target_gross_pct=1.50, target_net_pct=0.0,
                    max_position_pct=0.30, max_sector_pct=1.50, min_position_pct=0.001),
        betas, tol_dollar=0.02, tol_beta=0.05,
    )
    assert abs(out.net_pct) <= 0.02
    assert abs(out.portfolio_beta) <= 0.05


def test_neutral_unbalanced_betas_rescales_buckets():
    # High-β longs, low-β shorts: dollar-neutral would leave portfolio_beta > 0.
    # The α rescale should bring it inside tolerance.
    longs = [_c("HI", "long", "buy", conf=80)]
    shorts = [_c("LO", "short", "open_short", conf=80)]
    betas = {"HI": 1.5, "LO": 0.6}
    out = construct_market_neutral(
        longs + shorts,
        Constraints(target_gross_pct=1.0, target_net_pct=0.0,
                    max_position_pct=1.0, max_sector_pct=1.0, min_position_pct=0.0001),
        betas, tol_dollar=0.05, tol_beta=0.05,
    )
    assert abs(out.portfolio_beta) <= 0.05 or out.diagnostics["alpha_clamped"]


def test_neutral_alpha_clamp_logs_partial_breach():
    # Extreme β mismatch: longs at β=3, shorts at β=0.1. α = sqrt(0.1/3) ≈ 0.18,
    # gets clamped to 0.7. Expect partial-breach entry.
    longs = [_c("HOT", "long", "buy", conf=80)]
    shorts = [_c("DULL", "short", "open_short", conf=80)]
    betas = {"HOT": 3.0, "DULL": 0.1}
    out = construct_market_neutral(
        longs + shorts,
        Constraints(target_gross_pct=1.0, target_net_pct=0.0,
                    max_position_pct=1.0, max_sector_pct=1.0, min_position_pct=0.0001),
        betas, tol_dollar=0.02, tol_beta=0.05,
    )
    assert out.diagnostics["alpha_clamped"] is True
    assert any(r.get("reason") == "neutrality_partial_breach" for r in out.rejected)


def test_neutral_idempotent_same_inputs():
    cands = (
        [_c(f"L{i}", "long", "buy", conf=80, sector=f"S{i%2}") for i in range(3)] +
        [_c(f"S{i}", "short", "open_short", conf=80, sector=f"S{i%2}") for i in range(3)]
    )
    betas = {c.ticker: 1.0 + 0.1 * i for i, c in enumerate(cands)}
    cons = Constraints(target_gross_pct=1.50, target_net_pct=0.0,
                       max_position_pct=0.40, max_sector_pct=1.50, min_position_pct=0.001)
    a = construct_market_neutral(cands, cons, betas)
    b = construct_market_neutral(cands, cons, betas)
    assert a.target_weights == b.target_weights


# ---------- Beta estimator ----------

def test_ols_beta_recovers_known_slope():
    # ticker = 1.5 * benchmark + noise-free
    r_b = [0.01, -0.02, 0.005, 0.012, -0.008, 0.015, -0.003, 0.007, -0.011, 0.009]
    r_t = [1.5 * x for x in r_b]
    beta, r2 = _ols_beta(r_t, r_b)
    assert beta == pytest.approx(1.5, abs=1e-6)
    assert r2 == pytest.approx(1.0, abs=1e-6)


def test_ols_beta_zero_variance_returns_zero():
    r_b = [0.0] * 5
    r_t = [0.01] * 5
    beta, r2 = _ols_beta(r_t, r_b)
    assert beta == 0.0
    assert r2 == 0.0


@dataclass
class _Bar:
    ticker: str
    date: date
    open: Decimal = Decimal("100")
    high: Decimal = Decimal("100")
    low: Decimal = Decimal("100")
    close: Decimal = Decimal("100")
    adjusted_close: Decimal = Decimal("100")
    volume: int = 0


class _FakeProvider:
    """In-memory bars provider used to drive beta() without touching FMP."""

    def __init__(self, series: dict[str, dict[date, float]]):
        self.series = series
        self.calls: list[tuple[str, date, date, date]] = []

    def get_daily_bars(self, ticker, start, end, *, as_of):
        self.calls.append((ticker, start, end, as_of))
        s = self.series.get(ticker, {})
        # Enforce no look-ahead at the provider boundary.
        return [
            _Bar(ticker=ticker, date=d,
                 close=Decimal(str(px)), adjusted_close=Decimal(str(px)))
            for d, px in sorted(s.items())
            if start <= d <= end and d <= as_of
        ]


@pytest.mark.django_db
def test_compute_beta_uses_no_lookahead_and_caches():
    # Construct a deterministic price series for the trailing window.
    today = date(2024, 6, 28)
    days = [today - timedelta(days=i) for i in range(0, 365)][::-1]
    bm_prices = {}
    tk_prices = {}
    px_b, px_t = 100.0, 100.0
    for i, d in enumerate(days):
        rb = 0.001 if (i % 2 == 0) else -0.001
        bm_prices[d] = px_b * (1 + rb)
        tk_prices[d] = px_t * (1 + 1.2 * rb)
        px_b = bm_prices[d]
        px_t = tk_prices[d]
    # Add a FUTURE bar after as_of that should not influence beta.
    future = today + timedelta(days=10)
    bm_prices[future] = 999.0
    tk_prices[future] = 999.0

    provider = _FakeProvider({"SPY": bm_prices, "AAA": tk_prices})
    res = compute_beta("AAA", "SPY", today, window_days=60, data_provider=provider)
    # The provider receives end=as_of, so future bars cannot leak.
    for (_t, _start, end, as_of) in provider.calls:
        assert end <= as_of
    assert res.n_observations >= 30
    assert res.beta == pytest.approx(1.2, abs=0.05)
    assert res.reliable is True

    # Re-run hits the cache (no new provider calls).
    n_before = len(provider.calls)
    res2 = compute_beta("AAA", "SPY", today, window_days=60, data_provider=provider)
    assert len(provider.calls) == n_before
    assert res2.beta == pytest.approx(res.beta, abs=1e-3)


@pytest.mark.django_db
def test_compute_beta_marks_short_history_unreliable():
    today = date(2024, 6, 28)
    # Only 20 days of data — below MIN_OBS=60.
    days = [today - timedelta(days=i) for i in range(0, 25)][::-1]
    bm = {d: 100.0 + i for i, d in enumerate(days)}
    tk = {d: 100.0 + 0.5 * i for i, d in enumerate(days)}
    provider = _FakeProvider({"SPY": bm, "AAA": tk})
    res = compute_beta("AAA", "SPY", today, window_days=252, data_provider=provider)
    assert res.reliable is False
