"""Review (portmisc): Manual Book — proof tests for findings.

F-stale-daily-mark : the `daily` cadence mark can lag several sessions with
                     NO stale badge because FmpProvider._ensure_bars_cached
                     skips the fetch when >=60% of the 14-day window is cached
                     (and no nightly task refreshes manual-book tickers).
F-refresh-noop     : POST /api/portfolio/refresh-marks/ only clears Redis, so
                     the explicit "refresh" cannot fix the stale daily mark.
F-500s             : unvalidated numeric input -> unhandled exceptions (500).
F-no-margin        : shorts have no exposure / margin check at all.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.data.models import DailyBar
from apps.data.providers.fmp import FmpProvider
from apps.portfolios.manual_book import (
    ManualBookError,
    close_or_reduce_position,
    get_or_create_manual_book,
    open_or_increase_position,
)
from apps.portfolios.valuation import get_mark

User = get_user_model()


# ---------------------------------------------------------------------------
# fixtures (same shape as tests/test_manual_book.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def in_memory_mark_cache():
    store: dict[str, str] = {}

    def _get(key: str):
        raw = store.get(key)
        return json.loads(raw) if raw else None

    def _set(key: str, value, ttl_seconds: int = 86_400):
        store[key] = json.dumps(value, default=str)

    class _FakeRedis:
        def get(self, key):
            return store.get(key)

        def set(self, key, value, *args, **kwargs):
            store[key] = value if isinstance(value, str) else json.dumps(value)

        def setex(self, key, ttl, value):
            store[key] = value if isinstance(value, str) else json.dumps(value)

        def delete(self, *keys):
            for k in keys:
                store.pop(k, None)

    with (
        patch("apps.portfolios.valuation.cache_get", side_effect=_get),
        patch("apps.portfolios.valuation.cache_set", side_effect=_set),
        patch("apps.data.cache._redis", return_value=_FakeRedis()),
    ):
        yield store


@pytest.fixture(autouse=True)
def clear_refresh_marks_rate_limit():
    from apps.portfolios.manual_book_views import PortfolioRefreshMarksView

    PortfolioRefreshMarksView._last_refresh_at.clear()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="portmisc@example.com", password="x")


@pytest.fixture
def client(user) -> APIClient:
    c = APIClient(raise_request_exception=False)
    c.force_authenticate(user)
    return c


def _bar(ticker: str, on: date, close: float) -> None:
    DailyBar.objects.create(
        ticker=ticker, date=on, open=close, high=close, low=close, close=close,
        adjusted_close=close, volume=1_000_000, source="fmp",
    )


class _RecordingHttp:
    """Stand-in for the httpx client: records every GET and answers with a
    fresher bar so we can tell whether FMP was consulted at all."""

    def __init__(self, fresh_rows):
        self.calls: list[str] = []
        self._rows = fresh_rows

    def get(self, url, params=None):
        self.calls.append(url)
        rows = self._rows

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return rows

        return _Resp()


# ---------------------------------------------------------------------------
# F-stale-daily-mark
# ---------------------------------------------------------------------------


def test_daily_mark_lags_three_sessions_without_stale_badge_and_never_calls_fmp(db, user):
    """FIXED: the DailyBar cache is now judged on the FRESHNESS of its tail, not
    just on row count.

    Scenario (calendar): the user viewed the book on a Monday (bars fetched
    through that Monday). It is now Thursday of the same week. The DB holds 8
    bars inside the 14-day window — still >= the old int(14*0.6)=8 coverage
    heuristic that used to short-circuit the fetch — but the newest is 3
    sessions old, so FMP IS consulted and the "daily close" mark is its newest
    close, not Monday's."""
    today = date(2026, 9, 3)          # a Thursday
    monday = date(2026, 8, 31)        # this week's Monday
    # 8 trading days inside [today-14, today] ending on Monday:
    # Thu 20, Fri 21, Mon 24, Tue 25, Wed 26, Thu 27, Fri 28, Mon 31
    cached_days = [date(2026, 8, d) for d in (20, 21, 24, 25, 26, 27, 28, 31)]
    for i, d in enumerate(cached_days):
        _bar("AAPL", d, 100 + i)
    monday_close = 100 + len(cached_days) - 1
    assert DailyBar.objects.filter(ticker="AAPL", date__gte=today - timedelta(days=14),
                                   date__lte=today).count() == 8

    fresh = [
        {"date": "2026-09-01", "open": 200, "high": 200, "low": 200, "close": 200, "volume": 1},
        {"date": "2026-09-02", "open": 210, "high": 210, "low": 210, "close": 210, "volume": 1},
    ]
    http = _RecordingHttp(fresh)
    provider = FmpProvider(api_key="test-key", http=http)
    with patch("apps.portfolios.valuation.get_fmp_provider", return_value=provider):
        mark = get_mark("AAPL", today, user=user)

    assert mark is not None
    assert http.calls, "a 3-session-old tail must trigger a refetch, not short-circuit"
    assert mark.as_of == date(2026, 9, 2), "mark is FMP's newest close, not Monday's"
    assert mark.price == Decimal("210")
    assert mark.age_days == 1
    assert mark.stale is False
    # ...and specifically NOT the stale cached bar the coverage heuristic served.
    assert (mark.as_of, mark.price) != (monday, Decimal(str(monday_close)))


def test_refresh_marks_endpoint_cannot_unstick_the_stale_daily_mark(
    client, user, in_memory_mark_cache,
):
    """FIXED: the explicit refresh clears the Redis key AND the next read now
    re-consults FMP, because the cached DailyBar tail is stale. Both the first
    read and the post-refresh read carry the newest real close instead of the
    3-day-old bar the coverage heuristic used to pin them to."""
    today = date.today()
    # 8 bars in the window, the newest 3 calendar days ago.
    days = []
    d = today - timedelta(days=3)
    while len(days) < 8:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    for i, dd in enumerate(sorted(days)):
        _bar("MSFT", dd, 50 + i)
    newest = max(days)

    http = _RecordingHttp([
        {"date": (today - timedelta(days=1)).isoformat(), "open": 999, "high": 999,
         "low": 999, "close": 999, "volume": 1},
    ])
    provider = FmpProvider(api_key="test-key", http=http)
    with patch("apps.portfolios.valuation.get_fmp_provider", return_value=provider):
        open_or_increase_position(
            user=user, ticker="MSFT", side="long", quantity=Decimal("10"),
            entry_price=Decimal("50"),
        )
        first = client.get("/api/portfolio/").json()
        r = client.post("/api/portfolio/refresh-marks/")
        assert r.status_code == 200
        second = r.json()

    fresh_day = (today - timedelta(days=1)).isoformat()
    assert http.calls, "the stale cached tail must be refetched, not short-circuited"
    assert first["positions"][0]["mark_as_of"] == fresh_day != newest.isoformat()
    assert second["positions"][0]["mark_as_of"] == fresh_day
    assert Decimal(second["positions"][0]["mark_price"]) == Decimal("999")
    assert second["positions"][0]["mark_stale"] is False


# ---------------------------------------------------------------------------
# F-500s — unvalidated numeric / id input reaches Decimal()/ORM and explodes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "AAPL", "side": "long", "quantity": "abc", "entry_price": "10"},
        {"ticker": "AAPL", "side": "long", "quantity": "10", "entry_price": "1e999"},
        {"ticker": "AAPL", "side": "short", "quantity": "Infinity", "entry_price": "10"},
        {"ticker": "AAPL", "side": "short", "quantity": "1e30", "entry_price": "10"},
        {"ticker": "AAPL", "side": "long", "quantity": "10", "entry_price": "10",
         "source_run": "abc"},
        {"ticker": ["AAPL"], "side": "long", "quantity": "10", "entry_price": "10"},
    ],
)
def test_position_post_invalid_numeric_input_is_500_not_400(client, payload):
    with patch("apps.portfolios.valuation.get_fmp_provider", side_effect=RuntimeError("no key")):
        r = client.post("/api/portfolio/positions/", payload, format="json")
    assert r.status_code == 500, (r.status_code, r.content[:200])


@pytest.mark.parametrize("amount", ["abc", "Infinity", "1e30", "NaN"])
def test_cash_post_invalid_amount_is_500_not_400(client, amount):
    with patch("apps.portfolios.valuation.get_fmp_provider", side_effect=RuntimeError("no key")):
        r = client.post(
            "/api/portfolio/cash/", {"kind": "deposit", "amount": amount}, format="json",
        )
    assert r.status_code == 500, (r.status_code, r.content[:200])


def test_close_and_patch_invalid_numeric_input_is_500(client, user):
    with patch("apps.portfolios.valuation.get_fmp_provider", side_effect=RuntimeError("no key")):
        res = open_or_increase_position(
            user=user, ticker="AAPL", side="long", quantity=Decimal("10"),
            entry_price=Decimal("10"),
        )
        pid = res.position.id
        r1 = client.post(f"/api/portfolio/positions/{pid}/close/",
                         {"exit_price": "abc"}, format="json")
        r2 = client.post(f"/api/portfolio/positions/{pid}/close/",
                         {"exit_price": "10", "quantity": "x"}, format="json")
        r3 = client.patch(f"/api/portfolio/positions/{pid}/",
                          {"avg_cost": "abc"}, format="json")
    assert (r1.status_code, r2.status_code, r3.status_code) == (500, 500, 500)


def test_borrow_lookup_bad_as_of_is_500(client):
    r = client.get("/api/borrow/AAPL/?as_of=not-a-date")
    assert r.status_code == 500


def test_fund_history_huge_days_is_500(client, user):
    from apps.portfolios.models import AutonomousFund

    AutonomousFund.objects.create(owner=user, name="f")
    r = client.get("/api/fund/history/?days=999999999")
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# F-no-margin — a $100K book can short $10M of stock; covering is then blocked
# ---------------------------------------------------------------------------


def test_short_has_no_exposure_or_margin_check(db, user):
    pf = get_or_create_manual_book(user)
    assert pf.cash_balance == Decimal("100000")
    # 100,000 shares @ $100 = $10,000,000 short notional on a $100K book.
    res = open_or_increase_position(
        user=user, ticker="XYZ", side="short", quantity=Decimal("100000"),
        entry_price=Decimal("100"),
    )
    pf.refresh_from_db()
    assert pf.cash_balance == Decimal("10100000.00")  # proceeds credited, no margin
    # Stock +2% → a $200K loss on a $100K book, and the user cannot even cover:
    with pytest.raises(ManualBookError) as exc:
        close_or_reduce_position(
            user=user, position_id=res.position.id, exit_price=Decimal("102"),
        )
    assert "below zero" in str(exc.value)
