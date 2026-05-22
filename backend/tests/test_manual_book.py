"""P3: Manual Book (Portfolio tab + position entry) tests.

Covers the suggestion engine, quantity policy, lifecycle, ledger, isolation
regression, valuation, and API/permission validation.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.data.models import DailyBar, MacroSnapshot
from apps.portfolios.manual_book import (
    ManualBookError,
    adjust_cash,
    close_or_reduce_position,
    edit_position,
    get_or_create_manual_book,
    open_or_increase_position,
)
from apps.portfolios.models import LedgerEntry, Portfolio, Position
from apps.portfolios.quantity_policy import (
    QuantityPolicy,
    round_quantity_for_open,
    validate_quantity_for_mode,
)
from apps.portfolios.suggestion import suggest_position
from apps.portfolios.valuation import (
    free_cash,
    reserved_short_proceeds,
    value_portfolio,
)
from apps.runs.models import Decision, Run

User = get_user_model()


def _bar(ticker: str, on: date, close: float) -> None:
    DailyBar.objects.create(
        ticker=ticker,
        date=on,
        open=close,
        high=close,
        low=close,
        close=close,
        adjusted_close=close,
        volume=1_000_000,
        source="fmp",
    )


def _seed_mark(ticker: str, close: float, on: date | None = None) -> None:
    """Seed a single DailyBar so get_mark / valuation can mark to market."""
    today = on or date.today()
    DailyBar.objects.filter(ticker=ticker, source="fmp").delete()
    _bar(ticker, today, close)


class _StubProvider:
    """Drop-in for FmpProvider that returns whatever DailyBar rows exist."""

    def get_daily_bars(self, ticker, start, end, *, as_of):
        from apps.data.interfaces import Bar
        rows = DailyBar.objects.filter(
            ticker=ticker, source="fmp", date__gte=start, date__lte=min(end, as_of),
        ).order_by("date")
        return [
            Bar(
                ticker=r.ticker, date=r.date,
                open=r.open, high=r.high, low=r.low, close=r.close,
                adjusted_close=r.adjusted_close, volume=r.volume,
            )
            for r in rows
        ]


@pytest.fixture(autouse=True)
def stub_fmp_provider():
    """Replace the real FMP provider with one that reads only DailyBar rows
    so tests don't hit the network or get bars rewritten by the cache."""
    with patch("apps.portfolios.valuation.get_fmp_provider") as m:
        m.return_value = _StubProvider()
        yield m


@pytest.fixture(autouse=True)
def clear_mark_cache():
    """Mark cache is keyed on (ticker, date) so tests need a clean Redis."""
    try:
        from apps.data.cache import _redis
        client = _redis()
        for key in client.scan_iter("mark:fmp:*"):
            client.delete(key)
    except Exception:
        pass


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p3@example.com", password="x")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(email="p3-other@example.com", password="x")


@pytest.fixture
def client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


# ---------------------------------------------------------------------------
# Quantity policy
# ---------------------------------------------------------------------------


def test_quantity_policy_whole_floors_toward_zero():
    policy = QuantityPolicy.from_mode("whole")
    res = round_quantity_for_open(Decimal("12.7"), Decimal("100"), policy)
    assert res.quantity == Decimal("12")
    assert res.rounded_notional == Decimal("1200.00")
    assert res.residual_notional == Decimal("70.00")
    assert res.warning is None


def test_quantity_policy_whole_below_one_share_warns():
    policy = QuantityPolicy.from_mode("whole")
    res = round_quantity_for_open(Decimal("0.6"), Decimal("100"), policy)
    assert res.quantity == Decimal("0")
    assert res.rounded_notional == Decimal("0.00")
    assert "below one share" in (res.warning or "")


def test_quantity_policy_fractional_preserves_precision():
    policy = QuantityPolicy.from_mode("fractional")
    res = round_quantity_for_open(Decimal("12.123456789"), Decimal("100"), policy)
    # 6 decimal places retained
    assert res.quantity == Decimal("12.123456")
    assert res.warning is None


def test_validate_quantity_rejects_fractional_in_whole_mode():
    policy = QuantityPolicy.from_mode("whole")
    with pytest.raises(ValueError):
        validate_quantity_for_mode(Decimal("3.5"), policy)


# ---------------------------------------------------------------------------
# Manual book lifecycle
# ---------------------------------------------------------------------------


def test_get_or_create_manual_book_is_idempotent(db, user):
    p1 = get_or_create_manual_book(user)
    p2 = get_or_create_manual_book(user)
    assert p1.id == p2.id
    assert p1.kind == "manual"
    assert p1.cash_balance == Decimal("100000")


def test_manual_book_uniqueness_per_user(db, user):
    get_or_create_manual_book(user)
    with pytest.raises(IntegrityError):
        Portfolio.objects.create(
            user=user, name="dup", kind=Portfolio.KIND_MANUAL,
            cash_balance=Decimal("0"),
        )


def test_open_long_debits_cash_exactly(db, user):
    portfolio = get_or_create_manual_book(user)
    res = open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("200"),
    )
    portfolio.refresh_from_db()
    assert portfolio.cash_balance == Decimal("98000.00")
    assert res.position.quantity == Decimal("10")
    assert res.position.avg_cost == Decimal("200.0000")
    assert res.ledger_entry.kind == LedgerEntry.KIND_OPEN
    assert res.ledger_entry.cash_delta == Decimal("-2000.00")
    assert res.ledger_entry.cash_balance_after == Decimal("98000.00")


def test_open_short_credits_proceeds(db, user):
    portfolio = get_or_create_manual_book(user)
    res = open_or_increase_position(
        user=user, ticker="GME", side="short",
        quantity=Decimal("5"), entry_price=Decimal("100"),
    )
    portfolio.refresh_from_db()
    assert portfolio.cash_balance == Decimal("100500.00")
    assert res.position.quantity == Decimal("-5")
    # Free cash: reserved short proceeds = 5 * 100 = 500
    assert reserved_short_proceeds(portfolio) == Decimal("500.00")
    assert free_cash(portfolio) == Decimal("100000.00")


def test_increase_recomputes_weighted_avg_cost(db, user):
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("200"),
    )
    pos = Position.objects.get(ticker="AAPL")
    assert pos.quantity == Decimal("20")
    assert pos.avg_cost == Decimal("150.0000")


def test_cannot_flip_direction_without_close(db, user):
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    with pytest.raises(ManualBookError) as exc:
        open_or_increase_position(
            user=user, ticker="AAPL", side="short",
            quantity=Decimal("5"), entry_price=Decimal("110"),
        )
    assert exc.value.status_code == 409


def test_close_long_realized_pnl_math(db, user):
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    pos = Position.objects.get(ticker="AAPL")
    res = close_or_reduce_position(
        user=user, position_id=pos.id, exit_price=Decimal("120"),
    )
    # (120 - 100) * 10 = 200
    assert res.realized_pnl == Decimal("200.00")
    assert not Position.objects.filter(ticker="AAPL").exists()
    res.portfolio.refresh_from_db()
    # 99000 (after open) + 10 * 120 = 100200
    assert res.portfolio.cash_balance == Decimal("100200.00")


def test_close_short_realized_pnl_math(db, user):
    open_or_increase_position(
        user=user, ticker="GME", side="short",
        quantity=Decimal("5"), entry_price=Decimal("100"),
    )
    pos = Position.objects.get(ticker="GME")
    res = close_or_reduce_position(
        user=user, position_id=pos.id, exit_price=Decimal("80"),
    )
    # (100 - 80) * 5 = 100 (gain on short cover)
    assert res.realized_pnl == Decimal("100.00")
    res.portfolio.refresh_from_db()
    # 100500 (after open) - 5 * 80 = 100100
    assert res.portfolio.cash_balance == Decimal("100100.00")


def test_partial_close_keeps_position_with_realized_pnl(db, user):
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    pos = Position.objects.get(ticker="AAPL")
    close_or_reduce_position(
        user=user, position_id=pos.id, quantity=Decimal("4"),
        exit_price=Decimal("120"),
    )
    pos.refresh_from_db()
    assert pos.quantity == Decimal("6")
    assert pos.realized_pnl == Decimal("80.00")  # (120-100) * 4


def test_long_open_rejects_when_insufficient_free_cash(db, user):
    with pytest.raises(ManualBookError):
        open_or_increase_position(
            user=user, ticker="AAPL", side="long",
            quantity=Decimal("10000"), entry_price=Decimal("200"),
        )


def test_long_open_cannot_consume_short_proceeds(db, user):
    portfolio = get_or_create_manual_book(user)
    open_or_increase_position(
        user=user, ticker="GME", side="short",
        quantity=Decimal("500"), entry_price=Decimal("100"),
    )
    portfolio.refresh_from_db()
    # cash_balance now 150000; reserved 50000; free 100000.
    with pytest.raises(ManualBookError):
        open_or_increase_position(
            user=user, ticker="AAPL", side="long",
            quantity=Decimal("600"), entry_price=Decimal("200"),  # needs 120k
        )


def test_withdrawal_uses_free_cash_not_reserved(db, user):
    open_or_increase_position(
        user=user, ticker="GME", side="short",
        quantity=Decimal("500"), entry_price=Decimal("100"),
    )
    # cash 150000, reserved 50000, free 100000 — 80k withdrawal allowed
    res = adjust_cash(user=user, kind="withdrawal", amount=Decimal("80000"))
    assert res.portfolio.cash_balance == Decimal("70000.00")
    # 30k more would exceed free 20k → rejected
    with pytest.raises(ManualBookError):
        adjust_cash(user=user, kind="withdrawal", amount=Decimal("30000"))


def test_ledger_invariant_sum_matches_cash_balance(db, user):
    portfolio = get_or_create_manual_book(user)
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    adjust_cash(user=user, kind="deposit", amount=Decimal("5000"))
    pos = Position.objects.get(ticker="AAPL")
    close_or_reduce_position(
        user=user, position_id=pos.id, quantity=Decimal("3"),
        exit_price=Decimal("110"),
    )
    portfolio.refresh_from_db()
    total_delta = sum(
        (e.cash_delta for e in LedgerEntry.objects.filter(portfolio=portfolio)),
        Decimal("0"),
    )
    # Initial deposit entry is the $100k seed.
    assert total_delta == portfolio.cash_balance


def test_edit_position_writes_audit_only(db, user):
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    pos = Position.objects.get(ticker="AAPL")
    portfolio = pos.portfolio
    cash_before = portfolio.cash_balance
    edit_position(
        user=user, position_id=pos.id, quantity=Decimal("12"),
        note="fixed entry typo",
    )
    pos.refresh_from_db()
    portfolio.refresh_from_db()
    assert pos.quantity == Decimal("12")
    assert portfolio.cash_balance == cash_before  # no cash mutation
    last_entry = LedgerEntry.objects.filter(portfolio=portfolio).first()
    assert last_entry.kind == LedgerEntry.KIND_EDIT
    assert last_entry.cash_delta == Decimal("0.00")


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def test_value_portfolio_marks_long_and_short(db, user):
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    open_or_increase_position(
        user=user, ticker="GME", side="short",
        quantity=Decimal("5"), entry_price=Decimal("50"),
    )
    _seed_mark("AAPL", 120)
    _seed_mark("GME", 40)
    portfolio = get_or_create_manual_book(user)
    valuation = value_portfolio(portfolio)
    # cash: 100000 - 1000 + 250 = 99250
    assert valuation.cash_balance == Decimal("99250.00")
    aapl = next(p for p in valuation.positions if p.ticker == "AAPL")
    gme = next(p for p in valuation.positions if p.ticker == "GME")
    assert aapl.market_value == Decimal("1200.00")
    assert aapl.unrealized_pnl == Decimal("200.00")
    assert gme.market_value == Decimal("-200.00")  # short qty=-5, mark 40
    assert gme.unrealized_pnl == Decimal("50.00")  # (50-40)*5
    # total = cash + 1200 - 200 = 100250
    assert valuation.total_value == Decimal("100250.00")


def test_value_portfolio_stale_mark_warning(db, user):
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("10"), entry_price=Decimal("100"),
    )
    _seed_mark("AAPL", 105, on=date.today() - timedelta(days=10))
    portfolio = get_or_create_manual_book(user)
    valuation = value_portfolio(portfolio)
    aapl = next(p for p in valuation.positions if p.ticker == "AAPL")
    assert aapl.mark_stale is True
    assert any("Stale mark" in w for w in aapl.warnings)


# ---------------------------------------------------------------------------
# Isolation: strategies must reject the manual book.
# ---------------------------------------------------------------------------


def test_strategy_serializer_rejects_manual_book(db, user):
    from apps.portfolios.serializers import StrategySerializer
    from apps.portfolios.models import Universe

    manual = get_or_create_manual_book(user)
    universe = Universe.objects.create(name="t-univ", description="t", is_active=True)

    # Build a minimal request mock with the user.
    class _Req:
        pass
    req = _Req()
    req.user = user

    s = StrategySerializer(
        data={"name": "x", "kind": "long_only", "universe": universe.id,
              "portfolio": manual.id},
        context={"request": req},
    )
    assert not s.is_valid()
    assert "portfolio" in s.errors


# ---------------------------------------------------------------------------
# Suggestion engine
# ---------------------------------------------------------------------------


def _make_run_with_decision(user, ticker="AAPL", action="buy",
                            target_weight_signed=3.0, side="long",
                            confidence=80, status="done"):
    run = Run.objects.create(
        user=user, tickers=[ticker], status=status,
        as_of_date=date.today(),
    )
    decision = Decision.objects.create(
        run=run, ticker=ticker, action=action, confidence=confidence,
        target_weight_signed=Decimal(str(target_weight_signed)),
        target_weight_pct=Decimal(str(target_weight_signed)).copy_abs(),
        side=side,
    )
    return run, decision


def test_suggestion_uses_run_decision_target_weight(db, user):
    portfolio = get_or_create_manual_book(user)
    _seed_mark("AAPL", 200)
    MacroSnapshot.objects.create(
        as_of_date=date.today(), growth_quadrant="expansion",
        inflation_regime="moderate", yield_curve_state="normal",
        policy_stance="neutral", narrative="t",
    )
    _, decision = _make_run_with_decision(user, target_weight_signed=4)
    s = suggest_position(decision=decision, portfolio=portfolio)
    assert s.side == "long"
    # base 4% × macro (1.0 + 0.10 growth + 0.03 curve) = 4.52% — within cap
    assert s.suggested_weight_pct == Decimal("4.40")
    # whole-share rounding: $4520 @ $200 ≈ 22 shares
    assert s.suggested_quantity == Decimal("22")
    assert any(f.key == "run_decision" for f in s.factors)
    assert any(f.key == "macro" for f in s.factors)


def test_suggestion_hold_zero_weight(db, user):
    portfolio = get_or_create_manual_book(user)
    _seed_mark("AAPL", 200)
    _, decision = _make_run_with_decision(user, action="hold")
    s = suggest_position(decision=decision, portfolio=portfolio)
    assert s.suggested_weight_pct == Decimal("0.00")
    assert s.suggested_quantity == Decimal("0")
    assert any("hold" in w.lower() for w in s.warnings)


def test_suggestion_caps_at_20pct(db, user):
    portfolio = get_or_create_manual_book(user)
    _seed_mark("AAPL", 100)
    _, decision = _make_run_with_decision(user, target_weight_signed=50)
    s = suggest_position(decision=decision, portfolio=portfolio)
    assert s.suggested_weight_pct <= Decimal("20.0")
    assert any(f.key == "position_cap" for f in s.factors)


def test_suggestion_cash_cap_for_longs(db, user):
    portfolio = get_or_create_manual_book(user)
    portfolio.cash_balance = Decimal("1000")
    portfolio.save()
    _seed_mark("AAPL", 100)
    _, decision = _make_run_with_decision(user, target_weight_signed=50)
    s = suggest_position(decision=decision, portfolio=portfolio)
    # free cash 1000 / total 1000 = 100%, but capped at 20%
    assert s.suggested_weight_pct <= Decimal("20.0")


def test_suggestion_whole_share_below_one_warns(db, user):
    portfolio = get_or_create_manual_book(user)
    # $100k * 0.001 = $100 / $300 = 0.33 shares → 0 whole
    portfolio.cash_balance = Decimal("100000")
    portfolio.save()
    _seed_mark("AAPL", 300_000)  # absurd price → 0 whole shares
    _, decision = _make_run_with_decision(user, target_weight_signed=0.5)
    s = suggest_position(decision=decision, portfolio=portfolio)
    assert s.suggested_quantity == Decimal("0")
    assert any("below one share" in w.lower() for w in s.warnings)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_portfolio_overview_auto_creates(client):
    resp = client.get(reverse("portfolio-overview"))
    assert resp.status_code == 200
    assert resp.data["kind"] == "manual"
    assert Decimal(str(resp.data["cash_balance"])) == Decimal("100000.00")


@pytest.mark.django_db
def test_api_portfolio_position_post_and_close(client, user):
    resp = client.post(
        reverse("portfolio-positions"),
        {"ticker": "AAPL", "side": "long", "quantity": "10",
         "entry_price": "150"},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    pos = Position.objects.get(ticker="AAPL")
    resp = client.post(
        reverse("portfolio-position-close", args=[pos.id]),
        {"exit_price": "160"},
        format="json",
    )
    assert resp.status_code == 200
    assert Decimal(resp.data["realized_pnl"]) == Decimal("100.00")
    assert not Position.objects.filter(id=pos.id).exists()


@pytest.mark.django_db
def test_api_rejects_other_users_run(client, other_user):
    run = Run.objects.create(
        user=other_user, tickers=["AAPL"], status="done",
        as_of_date=date.today(),
    )
    decision = Decision.objects.create(
        run=run, ticker="AAPL", action="buy", confidence=80, side="long",
    )
    resp = client.get(
        reverse("portfolio-position-suggestion") + f"?run={run.id}&decision={decision.id}",
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_api_cash_deposit_writes_ledger(client):
    resp = client.post(
        reverse("portfolio-cash"),
        {"kind": "deposit", "amount": "500"},
        format="json",
    )
    assert resp.status_code == 200
    assert Decimal(str(resp.data["portfolio"]["cash_balance"])) == Decimal("100500.00")


@pytest.mark.django_db
def test_api_position_post_rejects_fractional_in_whole_mode(client):
    resp = client.post(
        reverse("portfolio-positions"),
        {"ticker": "AAPL", "side": "long", "quantity": "3.5",
         "entry_price": "100"},
        format="json",
    )
    assert resp.status_code == 400
    assert "fractional" in str(resp.data["detail"]).lower()


# ---------------------------------------------------------------------------
# P3.1: Mark cadence + preferences + refresh-marks
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preferences_default_to_daily(client):
    resp = client.get(reverse("portfolio-preferences"))
    assert resp.status_code == 200
    assert resp.data["mark_cadence"] == "daily"
    assert resp.data["interval_minutes"] == 20


@pytest.mark.django_db
def test_preferences_put_updates_cadence(client):
    resp = client.put(
        reverse("portfolio-preferences"),
        {"mark_cadence": "delayed", "interval_minutes": 30},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["mark_cadence"] == "delayed"
    assert resp.data["interval_minutes"] == 30


@pytest.mark.django_db
def test_preferences_put_rejects_short_interval(client):
    resp = client.put(
        reverse("portfolio-preferences"),
        {"mark_cadence": "delayed", "interval_minutes": 3},
        format="json",
    )
    assert resp.status_code == 400
    assert "interval_minutes" in resp.data


@pytest.mark.django_db
def test_preferences_put_rejects_too_long_interval(client):
    resp = client.put(
        reverse("portfolio-preferences"),
        {"mark_cadence": "delayed", "interval_minutes": 2000},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_overview_embeds_preferences(client):
    resp = client.get(reverse("portfolio-overview"))
    assert resp.status_code == 200
    assert resp.data["preferences"]["mark_cadence"] == "daily"
    assert resp.data["preferences"]["interval_minutes"] == 20


@pytest.mark.django_db
def test_refresh_marks_invalidates_cache_and_returns_overview(client, user):
    from apps.data.cache import _redis
    # Seed a manual position + a fake cached mark.
    open_or_increase_position(
        user=user, ticker="AAPL", side="long",
        quantity=Decimal("5"), entry_price=Decimal("100"),
    )
    _seed_mark("AAPL", 150)
    # Populate the cache directly so we can detect invalidation.
    redis = _redis()
    key = f"mark:fmp:daily:AAPL:{date.today().isoformat()}"
    redis.set(key, '{"ticker":"AAPL","price":"999","as_of":"2026-05-21",'
                   '"age_days":1,"stale":false,"source":"fmp"}')
    resp = client.post(reverse("portfolio-refresh-marks"))
    assert resp.status_code == 200
    # The bogus cached price is gone — overview now uses the seeded $150.
    aapl = next(p for p in resp.data["positions"] if p["ticker"] == "AAPL")
    assert Decimal(str(aapl["mark_price"])) == Decimal("150")


@pytest.mark.django_db
def test_refresh_marks_rate_limited(client):
    r1 = client.post(reverse("portfolio-refresh-marks"))
    r2 = client.post(reverse("portfolio-refresh-marks"))
    assert r1.status_code == 200
    # Second call within 5s is rate-limited.
    assert r2.status_code == 429


def test_intraday_mark_caches_for_interval_minutes(db, user):
    from apps.portfolios.models import PortfolioPreferences
    from apps.portfolios.valuation import get_mark

    prefs, _ = PortfolioPreferences.objects.get_or_create(user=user)
    prefs.mark_cadence = "delayed"
    prefs.interval_minutes = 15
    prefs.save()

    # Patch the FMP provider to return a controlled quote and count calls.
    from datetime import datetime as dt_cls
    from datetime import timezone as dt_tz

    calls = {"n": 0}

    class _IntradayStub:
        def get_latest_quote(self, ticker):
            calls["n"] += 1
            return Decimal("123.45"), dt_cls.now(tz=dt_tz.utc)

    with patch("apps.portfolios.valuation.get_fmp_provider") as m:
        m.return_value = _IntradayStub()
        first = get_mark("MSFT", user=user)
        second = get_mark("MSFT", user=user)
    assert first is not None
    assert second is not None
    assert first.price == Decimal("123.45")
    # Redis cache should have served the second call.
    assert calls["n"] == 1
