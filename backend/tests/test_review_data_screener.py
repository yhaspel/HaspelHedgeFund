"""Adversarial review (reviewer: data) — market screener + screener agent.

Proof tests for:
  * F-SORT-NULL: ``pipeline._sort_key`` puts ``None`` LAST for ascending sorts
    but FIRST for descending sorts (the default direction) — rows with no
    value for the sort field rank as the best.
  * F-MOM-UNIT: ``momentum_*`` fields are declared ``unit="pct"`` and their
    description tells the user to "set min >= 20", but ``metrics.momentum``
    returns a RATIO; a 25% mover fails ``{"min": 20}``.
  * F-ENUM-FIRST: multi-value enum criteria push only ``values[0]`` to FMP,
    so every other selected sector/exchange/industry silently returns 0 rows.
  * F-SYNTHETIC-RANK: ``hedgefund_agents.screener.features`` substitutes
    sha1-derived random features for tickers with no bars and ranks them
    against real names; the synthetic distribution is biased so a no-data
    ticker wins the SHORT ranking almost always.
  * F-MOM6M: ``features.momentum_6m`` is ``closes[-1]/closes[0]`` over a
    400-calendar-day window — a ~13-month return labelled "6m".
  * F-SAVED-500: saving a screen without an FMP key and with an invalid
    criterion is an unhandled ``ScreenerValidationError`` (HTTP 500).
"""
from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.screener.pipeline import (
    ScreenResultRow,
    _passes_criterion,
    _sort_key,
    _stage1_params,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def _row(ticker: str, **overrides) -> ScreenResultRow:
    base = dict(
        ticker=ticker, name=ticker, exchange="NYSE", sector="Technology",
        industry="", is_etf=False, price=Decimal("10"), change_pct=None,
        gap_pct=None, rvol=None, volume=None, adv_14d=None, dollar_volume=None,
        market_cap=None, pe_ratio=None, eps=None, beta=None, momentum_1m=None,
        momentum_3m=None, momentum_6m=None, dist_52w_high=None,
        dist_52w_low=None, above_50d_ma=None, above_200d_ma=None,
        has_positive_catalyst=False, in_watchlist=False, warnings=[],
    )
    base.update(overrides)
    return ScreenResultRow(**base)


# ---------------------------------------------------------------------------
# F-SORT-NULL
# ---------------------------------------------------------------------------


def test_none_sorts_last_in_both_directions():
    """FIXED: rows missing the sort field are appended after every valued row,
    on ascending AND descending sorts."""
    from apps.screener.pipeline import _sort_rows

    rows = [
        _row("A", momentum_3m=0.30),
        _row("NODATA", momentum_3m=None),
        _row("B", momentum_3m=0.10),
    ]
    desc = _sort_rows(rows, "momentum_3m", reverse=True)
    assert [r.ticker for r in desc] == ["A", "B", "NODATA"]
    asc = _sort_rows(rows, "momentum_3m", reverse=False)
    assert [r.ticker for r in asc] == ["B", "A", "NODATA"]
    # The raw key is still only valid for rows that HAVE a value.
    assert _sort_key(rows[0], "momentum_3m") == (0, 0.30)


# ---------------------------------------------------------------------------
# F-MOM-UNIT
# ---------------------------------------------------------------------------


def test_momentum_field_unit_matches_metric_scale():
    """FIXED: momentum_* declare unit="ratio" (matching metrics.momentum, the
    shipped preset and the UI's own x100 formatter) and their descriptions
    tell the user the ratio scale."""
    from apps.screener.fields import FIELD_REGISTRY
    from apps.screener.metrics import momentum

    f = FIELD_REGISTRY["momentum_3m"]
    assert f.unit == "ratio"
    assert "set min ≥ 0.20" in f.description
    assert "in percent" not in f.description.lower()
    # 63-session +25% move: the metric is a ratio (0.25), and the field says so.
    closes = [Decimal("100")] * 64 + [Decimal("125")]
    assert momentum(closes, 63) == pytest.approx(0.25)
    row = _row("UP", momentum_3m=0.25)
    # Following the field's own instruction ("min >= 0.20") now admits it ...
    assert _passes_criterion(row, "momentum_3m", {"min": 0.20}) is True
    # ... and a +10% mover is correctly excluded.
    assert _passes_criterion(_row("X", momentum_3m=0.10), "momentum_3m", {"min": 0.20}) is False
    # gap_pct / change_pct keep unit="pct" and ARE in percentage points, so the
    # editor's "%" suffix stays right for those.
    from apps.screener.metrics import gap_pct

    assert FIELD_REGISTRY["gap_pct"].unit == "pct"
    assert gap_pct(Decimal("104"), Decimal("100")) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# F-ENUM-FIRST
# ---------------------------------------------------------------------------


def test_multi_value_sector_filter_only_requests_first_value():
    params = _stage1_params(
        asset_class="equity",
        criteria={"sector": {"values": ["Technology", "Healthcare", "Energy"]}},
    )
    # Stage 1 (the only place rows come from) asks FMP for Technology alone;
    # Healthcare / Energy rows can never reach stage 3.
    assert params["sector"] == "Technology"


# ---------------------------------------------------------------------------
# F-SYNTHETIC-RANK
# ---------------------------------------------------------------------------


class _BarsProvider:
    """Bars for ``real`` tickers (random walk seeded per ticker); nothing for
    anything else — as after a provider outage / new listing / thin cache."""

    def __init__(self, real: dict[str, float]) -> None:
        self.real = real  # ticker -> annual vol

    def get_daily_bars(self, ticker, start, end, *, as_of):
        from apps.data.interfaces import Bar

        if ticker not in self.real:
            return []
        rng = random.Random(f"{ticker}|{as_of.isoformat()}")
        sigma = self.real[ticker] / (252 ** 0.5)
        p = 100.0
        out = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                p *= 1.0 + rng.gauss(0.0003, sigma)
                out.append(
                    Bar(ticker=ticker, date=d, open=Decimal(str(round(p, 4))),
                        high=Decimal(str(round(p, 4))), low=Decimal(str(round(p, 4))),
                        close=Decimal(str(round(p, 4))),
                        adjusted_close=Decimal(str(round(p, 4))), volume=1)
                )
            d += dt.timedelta(days=1)
        return out


def test_no_data_ticker_is_excluded_from_the_ranking():
    """FIXED: a ticker with no bars is reported as excluded, never ranked."""
    from hedgefund_agents.screener.screener_agent import run_screener

    real = {f"R{i:02d}": vol for i, vol in enumerate([0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6])}
    members = [(t, "Technology") for t in real] + [("NODATA", "Technology")]
    provider = _BarsProvider(real)

    n = 40
    for k in range(n):
        as_of = dt.date(2025, 1, 6) + dt.timedelta(days=7 * k)
        out = run_screener(
            members=members, as_of_date=as_of, top_k_longs=3, top_k_shorts=3,
            provider=provider,
        )
        shorts = [c["ticker"] for c in out["short_candidates"]]
        longs = [c["ticker"] for c in out["long_candidates"]]
        # No synthetic row survives into either side of the ranking ...
        assert not any(
            c["features"]["synthetic"]
            for c in out["short_candidates"] + out["long_candidates"]
        )
        assert "NODATA" not in shorts and "NODATA" not in longs
        # ... and the drop is reported on the ranking output.
        assert out["excluded_no_data"] == ["NODATA"]
        assert out["excluded_no_data_count"] == 1
        assert out["universe_size_evaluated"] == len(members)
        assert out["universe_size_ranked"] == len(real)


# ---------------------------------------------------------------------------
# F-MOM6M
# ---------------------------------------------------------------------------


def test_momentum_6m_is_a_six_month_return():
    """FIXED: momentum_6m is ~127 sessions back, not closes[0] of a 400-day
    calendar window."""
    from hedgefund_agents.screener.features import compute_features

    class _Prov:
        def get_daily_bars(self, ticker, start, end, *, as_of):
            from apps.data.interfaces import Bar

            out = []
            d = start
            i = 0
            while d <= end:
                if d.weekday() < 5:
                    # First 100 sessions at 100, then flat at 200 for the rest
                    # (~176 sessions > 126) — a true 6-month return is 0%.
                    p = 100 if i < 100 else 200
                    out.append(Bar(ticker=ticker, date=d, open=p, high=p, low=p,
                                   close=Decimal(p), adjusted_close=Decimal(p), volume=1))
                    i += 1
                d += dt.timedelta(days=1)
            return out

    f = compute_features("X", "Tech", dt.date(2026, 9, 7), provider=_Prov())
    assert f.synthetic is False
    assert f.momentum_3m == pytest.approx(0.0)
    # ~176 flat sessions at the tail: a true 6-month (127-session) return is 0%.
    assert f.momentum_6m == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# F-SAVED-500
# ---------------------------------------------------------------------------


@override_settings(ALLOW_PLATFORM_DATA_KEYS=False)
def test_saved_screen_invalid_filter_without_fmp_key_is_500():
    user = User.objects.create_user(email="s1@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    client.raise_request_exception = False
    resp = client.post(
        "/api/screener/saved/",
        {"name": "bad", "asset_class": "equity",
         "filters": {"not_a_field": {"min": 1}}, "sort": {"field": "market_cap", "dir": "desc"}},
        format="json",
    )
    assert resp.status_code == 500, resp.status_code


def test_saved_screen_invalid_filter_with_fmp_key_is_400():
    user = User.objects.create_user(email="s2@x.com", password="x" * 12)
    client = APIClient()
    client.force_authenticate(user)
    resp = client.post(
        "/api/screener/saved/",
        {"name": "bad", "asset_class": "equity",
         "filters": {"not_a_field": {"min": 1}}, "sort": {"field": "market_cap", "dir": "desc"}},
        format="json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# F-CATALYST-DEAD — vocabulary/scale mismatch with the news agent
# ---------------------------------------------------------------------------


def test_has_positive_catalyst_uses_the_real_tags_and_scales():
    """FIXED: materiality is read on the news agent's 0-10 scale against its
    real tag vocabulary, direction comes from the sentiment classifier, and a
    blank/``noise`` tag no longer counts."""
    from django.utils import timezone

    from apps.data.models import MarketNewsItem, NewsItem
    from apps.screener.pipeline import _catalyst_tickers

    for i, (tag, score) in enumerate(
        (("earnings", 9.5), ("guidance_change", 8.0), ("mna", 10.0), ("noise", 1.0))
    ):
        NewsItem.objects.create(
            ticker="AAPL", published_at=timezone.now(), headline=f"h{i}", source="s",
            provider="fmp", url=f"https://c/{i}", materiality_score=score,
            materiality_tag=tag,
        )
    # Material, but nothing says the direction is positive yet.
    assert _catalyst_tickers(["AAPL"], dt.date.today()) == set()
    # The sentiment classifier scores the SAME story positive -> catalyst.
    MarketNewsItem.objects.create(
        provider="fmp", headline="h0", url="https://c/0",
        published_at=timezone.now(), symbols=["AAPL"], sentiment="bullish",
        sentiment_score=0.8,
    )
    assert _catalyst_tickers(["AAPL"], dt.date.today()) == {"AAPL"}

    # A blank tag with materiality 0.7/10 is NOT a catalyst any more, even with
    # positive sentiment: 0.7 is far below the 6/10 bar and "" is not an event.
    NewsItem.objects.create(
        ticker="MSFT", published_at=timezone.now(), headline="blank", source="s",
        provider="fmp", url="https://c/blank", materiality_score=0.7, materiality_tag="",
    )
    MarketNewsItem.objects.create(
        provider="fmp", headline="blank", url="https://c/blank",
        published_at=timezone.now(), symbols=["MSFT"], sentiment="bullish",
        sentiment_score=0.9,
    )
    assert _catalyst_tickers(["AAPL", "MSFT"], dt.date.today()) == {"AAPL"}

    # Material + negative sentiment stays out (a 10/10 litigation item is not a
    # positive catalyst).
    NewsItem.objects.create(
        ticker="TSLA", published_at=timezone.now(), headline="suit", source="s",
        provider="fmp", url="https://c/suit", materiality_score=10.0,
        materiality_tag="litigation",
    )
    MarketNewsItem.objects.create(
        provider="fmp", headline="suit", url="https://c/suit",
        published_at=timezone.now(), symbols=["TSLA"], sentiment="bearish",
        sentiment_score=-0.7,
    )
    assert _catalyst_tickers(["AAPL", "MSFT", "TSLA"], dt.date.today()) == {"AAPL"}


# ---------------------------------------------------------------------------
# F-SCREEN-FANOUT — a cold run used to issue 1 + 3 + 300*2 FMP calls; stage 2
# now reads DailyBar and lazily fills at most SCREENER_LAZY_FILL_MAX tickers.
# ---------------------------------------------------------------------------


def test_one_screen_run_issues_600_plus_fmp_calls_on_cold_cache():
    """FIXED: stage 2 enriches from the DB and lazily fills at most
    ``SCREENER_LAZY_FILL_MAX`` uncached tickers per run.

    Cold cache, 300 enriched candidates: 1 screener call + 3 quote batches +
    at most 2 bar endpoints x the lazy-fill cap — not 600 bar calls at up to
    7,200/min against a 750/min budget shared with the live pods.
    """
    from apps.data.providers.fmp import FmpProvider
    from apps.screener.datasource import FmpScreenerDataSource
    from apps.screener.pipeline import run_screen

    calls: list[str] = []

    class _Http:
        def get(self, url, params=None):
            calls.append(
                url.rsplit("/", 1)[-1]
                if "historical" not in url
                else url.split("/stable/")[-1]
            )

            class _R:
                def raise_for_status(self):
                    return None

                def json(self):
                    if url.endswith("/company-screener"):
                        return [
                            {"symbol": f"S{i:03d}", "companyName": "x", "marketCap": 1e9,
                             "price": 10 + i, "volume": 1_000_000 + i, "isEtf": False}
                            for i in range(350)
                        ]
                    if url.endswith("/batch-quote"):
                        return []  # non-premium key → EOD fallback path
                    return []  # bars endpoints: nothing cached, nothing returned

            return _R()

    ds = FmpScreenerDataSource(FmpProvider(api_key="k", http=_Http()))  # type: ignore[arg-type]
    filters = {
        "asset_class": "equity", "criteria": {},
        "sort": {"field": "market_cap", "dir": "desc"}, "limit": 200,
    }
    with override_settings(SCREENER_LAZY_FILL_MAX=40):
        result = run_screen(filters, user=None, datasource=ds, use_cache=False)
    assert result.truncated is True
    n_screener = sum(1 for c in calls if c == "company-screener")
    n_quotes = sum(1 for c in calls if c == "batch-quote")
    n_full = sum(1 for c in calls if c.startswith("historical-price-eod/full"))
    n_adj = sum(1 for c in calls if c.startswith("historical-price-eod/dividend-adjusted"))
    assert (n_screener, n_quotes) == (1, 3)
    # Bounded: at most the lazy-fill cap of tickers reach the bar endpoints.
    assert n_full <= 40 and n_adj <= 40
    assert len(calls) <= 1 + 3 + 2 * 40
    # And every row that could not be enriched says so, rather than being
    # ranked on nulls.
    partial = [r for r in result.rows if r.enrichment == "partial"]
    assert partial, "cold cache must leave unenriched rows marked"
    assert all(
        any("not enriched" in w for w in r.warnings) for r in partial
    )
    assert any(w.startswith("Price history:") for w in result.warnings)


def test_lazy_fill_cap_is_configurable_and_zero_means_db_only():
    from apps.data.providers.fmp import FmpProvider
    from apps.screener.datasource import FmpScreenerDataSource
    from apps.screener.pipeline import run_screen

    calls: list[str] = []

    class _Http:
        def get(self, url, params=None):
            calls.append(
                url.rsplit("/", 1)[-1]
                if "historical" not in url
                else url.split("/stable/")[-1]
            )

            class _R:
                def raise_for_status(self):
                    return None

                def json(self):
                    if url.endswith("/company-screener"):
                        return [
                            {"symbol": f"S{i:03d}", "companyName": "x", "marketCap": 1e9,
                             "price": 10 + i, "volume": 1_000_000 + i, "isEtf": False}
                            for i in range(350)
                        ]
                    return []

            return _R()

    ds = FmpScreenerDataSource(FmpProvider(api_key="k", http=_Http()))  # type: ignore[arg-type]
    filters = {
        "asset_class": "equity", "criteria": {},
        "sort": {"field": "market_cap", "dir": "desc"}, "limit": 200,
    }
    with override_settings(SCREENER_LAZY_FILL_MAX=0):
        result = run_screen(filters, user=None, datasource=ds, use_cache=False)
    assert not [c for c in calls if c.startswith("historical-price-eod")]
    assert all(r.enrichment == "partial" for r in result.rows)
