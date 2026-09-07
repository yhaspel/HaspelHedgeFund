"""Adversarial review (reviewer: data) — FMP bar cache heuristic + regime staleness.

Proof tests for:
  * F-CACHE-TAIL: ``FmpProvider._ensure_bars_cached`` skips the network when
    ``existing >= 0.6 * calendar_days``. Real trading-day density is ~0.69, so
    up to ~13% of the requested window (the most recent part) can be missing
    and no fetch happens. For the 11-year Markov window that is ~500 calendar
    days of stale tail; for a 420-day TSMOM window ~55 days.
  * F-REGIME-STALE-FLAG: ``RegimeSnapshot.stale`` is derived from
    ``as_of_date`` (the fit date) — not from ``last_price_date`` (the newest
    bar actually used) — so a fit on months-old bars is labelled fresh and
    counted in the Markov consensus vote.
  * F-TICKER-CASE: the bar cache is keyed on the raw ticker string; a
    lower-case caller gets a second FMP fetch and a duplicate row set.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from apps.data.models import DailyBar, RegimeSnapshot
from apps.data.providers.fmp import FmpProvider

pytestmark = pytest.mark.django_db


class _RecordingHttp:
    """httpx.Client stand-in: records every GET and answers with ``payload``."""

    def __init__(self, payload: Any = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.payload = payload if payload is not None else []

    def get(self, url: str, params: dict | None = None):
        self.calls.append((url, dict(params or {})))
        http = self

        class _R:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> Any:
                return http.payload

        return _R()


def _seed_trading_days(ticker: str, start: dt.date, end: dt.date, price: float = 100.0) -> int:
    """Insert one DailyBar per weekday in [start, end] — a slightly *denser*
    series than reality (no holidays), i.e. the most favourable case for the
    cache heuristic."""
    objs = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            objs.append(
                DailyBar(
                    ticker=ticker, date=d, open=price, high=price, low=price,
                    close=Decimal(str(price)), adjusted_close=Decimal(str(price)),
                    volume=1000, source="fmp",
                )
            )
        d += dt.timedelta(days=1)
    DailyBar.objects.bulk_create(objs, batch_size=2000)
    return len(objs)


# ---------------------------------------------------------------------------
# F-CACHE-TAIL
# ---------------------------------------------------------------------------


def test_regime_window_with_a_400_day_stale_tail_refetches():
    """FIXED: Markov refit window (as regime_persistence._fetch_bars computes
    it): ~4094 calendar days ending the day before as_of. Seed weekday bars for
    the whole window EXCEPT the last 400 days — the provider must go to FMP."""
    as_of = dt.date(2026, 9, 7)
    days_needed = int((2520 + 20) * 1.6) + 30  # == regime_persistence._fetch_bars
    start = as_of - dt.timedelta(days=days_needed)
    end = as_of - dt.timedelta(days=1)
    seeded_until = end - dt.timedelta(days=400)
    _seed_trading_days("XLRE", start, seeded_until)

    http = _RecordingHttp(payload=[])
    prov = FmpProvider(api_key="fake", http=http)  # type: ignore[arg-type]
    prov.get_daily_bars("XLRE", start, end, as_of=as_of)

    assert http.calls, "a 400-day-stale tail must trigger a refetch"
    assert any("historical-price-eod/full" in url for url, _ in http.calls)


def test_tsmom_window_with_a_50_day_stale_tail_refetches():
    """FIXED: tsmom_score / screener features use a 400-420 calendar-day
    window; a 50-day-old tail is no longer served silently."""
    as_of = dt.date(2026, 9, 7)
    start = as_of - dt.timedelta(days=420)
    seeded_until = as_of - dt.timedelta(days=50)
    _seed_trading_days("TLT", start, seeded_until)

    http = _RecordingHttp(payload=[])
    prov = FmpProvider(api_key="fake", http=http)  # type: ignore[arg-type]
    prov.get_daily_bars("TLT", start, as_of, as_of=as_of)

    assert http.calls, "a 50-day-stale tail must trigger a refetch"


def test_a_complete_and_current_window_is_still_served_from_cache():
    """The heuristic must still short-circuit when the cache really is
    complete — otherwise every read is an FMP call."""
    as_of = dt.date(2026, 9, 7)  # a Monday
    start = as_of - dt.timedelta(days=420)
    _seed_trading_days("SPY", start, as_of)

    http = _RecordingHttp(payload=[])
    prov = FmpProvider(api_key="fake", http=http)  # type: ignore[arg-type]
    bars = prov.get_daily_bars("SPY", start, as_of, as_of=as_of)

    assert http.calls == []
    assert bars and (as_of - bars[-1].date).days == 0


# ---------------------------------------------------------------------------
# F-REGIME-STALE-FLAG
# ---------------------------------------------------------------------------


def test_regime_snapshot_fit_on_old_bars_is_labelled_stale():
    from hedgefund_agents.macro.markov_regime import MarkovConfig
    from hedgefund_agents.macro.regime_persistence import (
        compute_markov_consensus,
        fit_and_persist,
        get_latest_snapshot,
    )

    as_of = dt.date(2026, 9, 7)
    last_bar = as_of - dt.timedelta(days=120)

    class _StaleProvider:
        """Returns bars that end 120 days before as_of (what the cache
        heuristic above hands the fitter) — 12x the 10-day staleness limit."""

        def get_daily_bars(self, ticker, start, end, *, as_of):
            out = []
            d = start
            p = 100.0
            i = 0
            while d <= last_bar:
                if d.weekday() < 5:
                    # deterministic zig-zag so all three states get populated
                    p *= 1.0 + (0.012 if (i // 15) % 2 == 0 else -0.011)
                    i += 1
                    from apps.data.interfaces import Bar
                    out.append(
                        Bar(
                            ticker=ticker, date=d, open=Decimal(str(round(p, 4))),
                            high=Decimal(str(round(p, 4))), low=Decimal(str(round(p, 4))),
                            close=Decimal(str(round(p, 4))),
                            adjusted_close=Decimal(str(round(p, 4))), volume=1,
                        )
                    )
                d += dt.timedelta(days=1)
            return out

    cfg = MarkovConfig(fit_lookback_observations=300)
    snap = fit_and_persist(
        ticker="XLRE", as_of_date=as_of, config=cfg, data_provider=_StaleProvider()
    )
    assert snap.last_price_date <= last_bar
    latest = get_latest_snapshot("XLRE", as_of_date=as_of, config=cfg)
    assert latest is not None
    # FIXED: 120-day-old prices -> stale, whatever the fit date says.
    assert latest.stale is True
    consensus = compute_markov_consensus(as_of_date=as_of, tickers=["XLRE"], config=cfg)
    assert consensus["available_count"] == 0
    assert consensus["stale_count"] == 1
    assert consensus["per_ticker"]["XLRE"]["stale"] is True
    # ... and the payload says WHICH price date it was judged on.
    assert consensus["per_ticker"]["XLRE"]["last_price_date"] == (
        snap.last_price_date.isoformat()
    )
    assert RegimeSnapshot.objects.filter(ticker="XLRE").count() == 1


def test_regime_snapshot_fit_on_current_bars_is_still_fresh():
    """The stricter rule must not label a genuinely current fit stale."""
    from hedgefund_agents.macro.markov_regime import MarkovConfig
    from hedgefund_agents.macro.regime_persistence import (
        get_latest_snapshot,
    )

    as_of = dt.date(2026, 9, 7)
    cfg = MarkovConfig(fit_lookback_observations=300)
    from apps.data.models import RegimeModel

    rm = RegimeModel.objects.create(
        ticker="SPY", as_of_date=as_of, model_type=cfg.model_type, config_hash=cfg.hash(),
        transition_matrix=[[0.9, 0.05, 0.05], [0.05, 0.9, 0.05], [0.05, 0.05, 0.9]],
        state_labels={}, stationary_distribution={}, fit_observations=1,
        observations_available=1, training_start_date=as_of, training_end_date=as_of,
    )
    RegimeSnapshot.objects.create(
        ticker="SPY", as_of_date=as_of, model_type=cfg.model_type, config_hash=cfg.hash(),
        source_model=rm, last_price_date=as_of - dt.timedelta(days=3),
        current_state="bull", current_state_persistence=0.9, bull_persistence=0.9,
        sideways_persistence=0.9, bear_persistence=0.9, bull_prob_1d=0.6,
        sideways_prob_1d=0.3, bear_prob_1d=0.1, bull_prob_5d=0.5,
        sideways_prob_5d=0.3, bear_prob_5d=0.2, bull_minus_bear_1d=0.5,
    )
    latest = get_latest_snapshot("SPY", as_of_date=as_of, config=cfg)
    assert latest is not None and latest.stale is False


# ---------------------------------------------------------------------------
# F-TICKER-CASE
# ---------------------------------------------------------------------------


def test_lowercase_ticker_shares_the_cache_and_never_duplicates_rows():
    """FIXED: the symbol is upper-cased before the cache is consulted."""
    as_of = dt.date(2026, 9, 5)
    start = as_of - dt.timedelta(days=30)
    payload = [
        {"date": (start + dt.timedelta(days=i)).isoformat(), "open": 1, "high": 1,
         "low": 1, "close": 1, "adjClose": 1, "volume": 1}
        for i in range(31)
        if (start + dt.timedelta(days=i)).weekday() < 5
    ]
    http = _RecordingHttp(payload=payload)
    prov = FmpProvider(api_key="fake", http=http)  # type: ignore[arg-type]
    upper_bars = prov.get_daily_bars("AAPL", start, as_of, as_of=as_of)
    n_calls_upper = len(http.calls)
    lower_bars = prov.get_daily_bars("aapl", start, as_of, as_of=as_of)
    assert len(http.calls) == n_calls_upper, "lower-case symbol must hit the cache"
    assert DailyBar.objects.filter(ticker="AAPL").count() == len(payload)
    assert DailyBar.objects.filter(ticker="aapl").count() == 0
    assert [b.date for b in lower_bars] == [b.date for b in upper_bars]
