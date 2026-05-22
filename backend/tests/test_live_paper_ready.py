"""P02e review: check_live_paper_ready management command + helpers."""
from __future__ import annotations

import datetime as dt
import json as _json
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.data.models import DailyBar
from apps.portfolios.management.commands.check_live_paper_ready import (
    check_caps_sane,
    check_universe_features,
    run_all_checks,
)
from apps.portfolios.models import (
    BorrowQuote,
    Portfolio,
    PortfolioStrategy,
    Universe,
    UniverseMembership,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


def _make_strategy(kind: str = "long_short", with_universe: bool = True) -> PortfolioStrategy:
    u = User.objects.create_user(email=f"lp{id(kind)}@x.com", password="x" * 12)
    universe = Universe.objects.create(
        name=f"u-{id(kind)}", description="test", source="manual"
    )
    if with_universe:
        for t in ("AAPL", "MSFT"):
            UniverseMembership.objects.create(
                universe=universe, ticker=t,
                effective_from=dt.date(2020, 1, 1), sector="Tech",
            )
    portfolio = Portfolio.objects.create(user=u, name="P")
    return PortfolioStrategy.objects.create(
        user=u, name="S", kind=kind, universe=universe, portfolio=portfolio,
        cost_ceiling_per_cycle_usd=Decimal("5.00"),
    )


def test_check_caps_sane_flags_zero_cap_p02e() -> None:
    s = _make_strategy()
    s.max_position_pct = Decimal("0")
    s.save(update_fields=["max_position_pct"])
    c = check_caps_sane(s)
    assert c.passed is False
    assert "max_position_pct" in c.detail


def test_check_universe_features_passes_when_bars_are_fresh_p02e() -> None:
    s = _make_strategy()
    today = dt.date.today()
    for t in ("AAPL", "MSFT"):
        DailyBar.objects.create(
            ticker=t, date=today, open=100, high=101, low=99, close=100,
            adjusted_close=100, volume=1_000_000, source="fmp",
        )
    c = check_universe_features(s)
    assert c.passed is True


def test_check_universe_features_flags_missing_data_p02e() -> None:
    s = _make_strategy()  # no DailyBar rows seeded.
    c = check_universe_features(s)
    assert c.passed is False
    assert "AAPL" in c.detail or "MSFT" in c.detail


def test_long_only_skips_borrow_check_p02e() -> None:
    s = _make_strategy(kind=PortfolioStrategy.KIND_LONG_ONLY)
    results = run_all_checks(s)
    borrow = next(r for r in results if r.name == "borrow_coverage")
    assert borrow.passed is True  # long-only short-circuits.
    assert "long-only" in borrow.detail.lower()


def test_long_short_with_no_borrow_quotes_fails_p02e() -> None:
    s = _make_strategy(kind=PortfolioStrategy.KIND_LONG_SHORT)
    results = run_all_checks(s)
    borrow = next(r for r in results if r.name == "borrow_coverage")
    assert borrow.passed is False  # 0% coverage.


def test_long_short_with_borrow_quotes_passes_p02e() -> None:
    s = _make_strategy(kind=PortfolioStrategy.KIND_LONG_SHORT)
    for t in ("AAPL", "MSFT"):
        BorrowQuote.objects.create(
            ticker=t, as_of_date=dt.date.today(),
            is_locatable=True, source="stub",
        )
    results = run_all_checks(s)
    borrow = next(r for r in results if r.name == "borrow_coverage")
    assert borrow.passed is True


def test_management_command_json_output_p02e() -> None:
    s = _make_strategy(kind=PortfolioStrategy.KIND_LONG_ONLY)
    out = StringIO()
    # Long-only with no DailyBar rows → universe_features fails → command
    # raises CommandError. We capture the JSON before the raise.
    with pytest.raises(CommandError):
        call_command("check_live_paper_ready",
                     "--strategy-id", str(s.pk), "--json", stdout=out)
    body = _json.loads(out.getvalue())
    assert body["strategy_id"] == s.pk
    assert body["ready"] is False
    names = {c["name"] for c in body["checks"]}
    assert {"llm_provider_key", "universe_features",
            "borrow_coverage", "caps_sane", "budget"} <= names