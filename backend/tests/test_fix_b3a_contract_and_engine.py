"""WP B3a — fixes with no pre-existing proof test.

Covers the create-contract rules the review did not name individually, the
engine_version stamp + its serializer contract, the deterministic path's
fold-boundary continuity (the council path is covered by
test_review_backtests_engine.py::test_E3_*), and the compare endpoint's
ownership / DONE requirement.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.backtests.models import Backtest, BacktestMetrics
from apps.backtests.serializers import (
    DEFAULT_UNIVERSE_20,
    MAX_UNIVERSE_SIZE,
    BacktestCreateSerializer,
)
from apps.data.models import DailyBar

User = get_user_model()
pytestmark = pytest.mark.django_db

START = dt.date(2023, 1, 2)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="b3a@x.test", password="pw-fake-123456789")


def _ser(user, **kw):
    data = {
        "name": "x", "universe": ["AAA"],
        "start_date": "2023-01-02", "end_date": "2025-12-31",
    }
    data.update(kw)
    return BacktestCreateSerializer(data=data, context={"request": SimpleNamespace(user=user)})


def _seed(ticker: str, n_cal: int, step: float = 0.001, start: dt.date = START) -> None:
    price = 100.0
    for i in range(n_cal):
        d = start + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        open_ = price
        price *= 1.0 + step
        DailyBar.objects.create(
            ticker=ticker, date=d, source="fmp", open=Decimal(f"{open_:.4f}"),
            high=Decimal(f"{max(open_, price):.4f}"), low=Decimal(f"{min(open_, price):.4f}"),
            close=Decimal(f"{price:.4f}"), adjusted_close=Decimal(f"{price:.4f}"), volume=1,
        )


# ---------------------------------------------------------------------------
# §1 — universe contract
# ---------------------------------------------------------------------------
def test_universe_is_uppercased_and_deduplicated(user):
    ser = _ser(user, universe=["aapl", " msft ", "AAPL", "brk.b"])
    assert ser.is_valid(), ser.errors
    assert ser.validated_data["universe"] == ["AAPL", "MSFT", "BRK.B"]


@pytest.mark.parametrize("universe", [
    ["AAPL", "not a ticker"],
    ["AAPL", "TOOOOOOOOOOOOOOOLONG"],
    ["AAPL", "$$$"],
    ["AAPL", 42],
    ["   "],
    "AAPL",
])
def test_malformed_universe_is_rejected(user, universe):
    ser = _ser(user, universe=universe)
    assert not ser.is_valid()
    assert "universe" in ser.errors


def test_oversized_universe_is_rejected(user):
    ser = _ser(user, universe=[f"T{i}" for i in range(MAX_UNIVERSE_SIZE + 1)])
    assert not ser.is_valid()
    assert "universe" in ser.errors
    ok = _ser(user, universe=[f"T{i}" for i in range(MAX_UNIVERSE_SIZE)])
    assert ok.is_valid(), ok.errors


def test_empty_or_absent_universe_falls_back_to_the_default_twenty(user):
    """The documented fallback stays — but the stored universe is never empty,
    which is what let a run 'complete' with a flat zero curve."""
    for payload in ({"universe": []}, {}):
        data = {"name": "x", "start_date": "2023-01-02", "end_date": "2025-12-31"}
        data.update(payload)
        ser = BacktestCreateSerializer(
            data=data, context={"request": SimpleNamespace(user=user)}
        )
        assert ser.is_valid(), ser.errors
        assert ser.validated_data["universe"] == DEFAULT_UNIVERSE_20


# ---------------------------------------------------------------------------
# §1 — remaining numeric floors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("is_window_days", 125),
    ("oos_window_days", 20),
    ("step_days", 0),
    ("n_candidates", 0),
    ("starting_cash", "0"),
    ("commission_bps", "-0.01"),
    ("spread_bps", "-1"),
    ("max_budget_usd", "0.04"),
    ("rebalance_frequency", "hourly"),
])
def test_each_field_floor_is_enforced(user, field, value):
    ser = _ser(user, **{field: value})
    assert not ser.is_valid(), f"{field}={value!r} accepted"
    assert field in ser.errors


def test_step_days_below_oos_window_is_rejected(user):
    ser = _ser(user, is_window_days=126, oos_window_days=63, step_days=21)
    assert not ser.is_valid()
    assert "overlapping OOS folds" in str(ser.errors)
    ok = _ser(user, is_window_days=126, oos_window_days=63, step_days=63)
    assert ok.is_valid(), ok.errors


# ---------------------------------------------------------------------------
# §2 — engine_version stamp + contract
# ---------------------------------------------------------------------------
def test_engine_version_defaults_to_2_and_is_stamped_on_run(user):
    from apps.backtests.walkforward import run_walkforward

    _seed("AAA", 160)
    bt = Backtest.objects.create(
        user=user, name="v", universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=147), is_window_days=126,
        oos_window_days=21, step_days=21, n_candidates=1,
        engine_mode=Backtest.RISK_PARITY, rebalance_frequency="monthly",
    )
    assert bt.engine_version == 2
    # An old row stamped v1 by the migration is re-stamped when it executes.
    Backtest.objects.filter(pk=bt.pk).update(engine_version=1)
    bt.refresh_from_db()
    run_walkforward(bt)
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE
    assert bt.engine_version == Backtest.ENGINE_VERSION == 2


def test_engine_version_is_exposed_on_list_and_detail(user):
    bt = Backtest.objects.create(
        user=user, name="v", universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=400), status=Backtest.DONE,
    )
    Backtest.objects.filter(pk=bt.pk).update(engine_version=1)
    c = APIClient()
    c.force_authenticate(user)
    row = c.get("/api/backtests/").json()["results"][0]
    assert row["engine_version"] == 1
    assert c.get(f"/api/backtests/{bt.id}/").json()["engine_version"] == 1


# ---------------------------------------------------------------------------
# §2a — deterministic path: the carried book is marked at the fold boundary
# ---------------------------------------------------------------------------
def test_deterministic_fold_boundary_marks_the_carried_book():
    """Both engine paths now mark a carried book to the prior session's close on
    a segment's first day. Without it the previous fold's last session was
    dropped from the stitched curve; a fresh book is unaffected (no positions)."""
    from apps.backtests.engine import (
        DETERMINISTIC_DEFAULTS,
        rebalance_dates_for,
        run_deterministic_segment,
        trading_days,
    )
    from apps.backtests.portfolio import SimulatedPortfolio

    _seed("AAA", 200, step=0.01)
    bt = SimpleNamespace(
        universe=["AAA"], starting_cash=Decimal("100000"), commission_bps=Decimal("0"),
        spread_bps=Decimal("0"), hold_semantics="hold_existing", financing_bps=Decimal("0"),
    )
    cfg = dict(DETERMINISTIC_DEFAULTS)
    all_days = trading_days(START, START + dt.timedelta(days=199), ["AAA"])
    # Start well past the 20-session vol lookback so the sizer is actually armed.
    days = all_days[60:]
    split = len(days) // 2
    a0, a1 = days[0], days[split - 1]
    b0, b1 = days[split], days[-1]

    def _seg(s, e, pf=None):
        d = trading_days(s, e, ["AAA"])
        return run_deterministic_segment(
            bt=bt, start=s, end=e, config=cfg,
            rebalance_dates=rebalance_dates_for(d, "weekly"), pf=pf,
        )

    pf = SimulatedPortfolio(starting_cash=100_000.0, commission_bps=0.0, spread_bps=0.0)
    seg_a = _seg(a0, a1, pf=pf)
    seg_b = _seg(b0, b1, pf=pf)
    whole = _seg(a0, b1)

    assert seg_a.positions_by_day[-1]                       # invested at the boundary
    # Fold B opens above fold A's close: the boundary session is not lost.
    assert seg_b.equity[0] > seg_a.equity[-1]
    # ...and the two-segment stitch matches one unbroken run over the same dates.
    assert seg_b.equity[-1] / seg_a.equity[0] == pytest.approx(
        whole.equity[-1] / whole.equity[0], rel=1e-6
    )
    # No liquidate-and-rebuy: fold B's opening day trades nothing.
    assert seg_b.fills_by_day[0] == []


# ---------------------------------------------------------------------------
# §4 — heartbeat
# ---------------------------------------------------------------------------
def test_progress_writes_a_heartbeat(user):
    from django.utils import timezone

    from apps.backtests.walkforward import _save_progress

    bt = Backtest.objects.create(
        user=user, name="hb", universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=400),
    )
    assert bt.heartbeat_at is None
    before = timezone.now()
    _save_progress(bt, 42, "fold 1/3")
    bt.refresh_from_db()
    assert bt.progress_pct == 42
    assert bt.heartbeat_at is not None and bt.heartbeat_at >= before


# ---------------------------------------------------------------------------
# §5 — compare ownership + DONE requirement
# ---------------------------------------------------------------------------
def _done(u, name="d"):
    bt = Backtest.objects.create(
        user=u, name=name, universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=400), status=Backtest.DONE,
    )
    BacktestMetrics.objects.create(backtest=bt, sharpe=Decimal("1.0"))
    return bt


def test_compare_requires_both_ids_to_be_done(user):
    a = _done(user, "a")
    b = Backtest.objects.create(
        user=user, name="queued", universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=400), status=Backtest.QUEUED,
    )
    c = APIClient()
    c.force_authenticate(user)
    r = c.post("/api/backtests/compare/",
               {"backtest_a_id": a.id, "backtest_b_id": b.id}, format="json")
    assert r.status_code == 400
    assert str(b.id) in r.json()["detail"]
    ok = c.post("/api/backtests/compare/",
                {"backtest_a_id": a.id, "backtest_b_id": _done(user, "b").id}, format="json")
    assert ok.status_code == 200


@pytest.mark.parametrize("payload", [
    {"backtest_a_id": None, "backtest_b_id": None},
    {"backtest_a_id": "not-an-id", "backtest_b_id": "nope"},
    {},
])
def test_compare_with_junk_ids_is_404_not_500(user, payload):
    c = APIClient()
    c.force_authenticate(user)
    r = c.post("/api/backtests/compare/", payload, format="json")
    assert r.status_code == 404


def test_compare_never_reaches_another_users_backtest(user):
    other = User.objects.create_user(email="b3a-other@x.test", password="pw-fake-123456789")
    mine = _done(user, "mine")
    theirs = _done(other, "theirs")
    c = APIClient()
    c.force_authenticate(user)
    for a_id, b_id in ((mine.id, theirs.id), (theirs.id, mine.id)):
        r = c.post("/api/backtests/compare/",
                   {"backtest_a_id": a_id, "backtest_b_id": b_id}, format="json")
        assert r.status_code == 404
