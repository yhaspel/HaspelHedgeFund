"""Adversarial review (reviewer: backtests) — create/estimate input validation.

FIXED by WP B3a §1. Each test now asserts the enforced contract; the bug it used
to prove is described in the past tense.

  * V1: ``step_days=0`` (or negative) was accepted by BacktestCreateSerializer
        and made ``generate_folds`` loop forever (unbounded ``folds`` list) —
        one Celery prefork slot burned until OOM. Now a 400, and
        ``generate_folds`` raises ValueError as a last line of defence.
  * V2: ``n_candidates=0`` was accepted; the council walk-forward then crashed
        with IndexError (``candidates[0]``) => FAILED. Now a 400.
  * V3: ``rebalance_frequency`` was free text: any value outside
        daily/weekly/monthly (e.g. "quarterly", "Weekly") silently became
        DAILY — the most expensive cadence (every ticker-day is an LLM prime).
        Now a 400 naming the allowed choices.
  * V4: ``starting_cash=0`` (what StrategyBacktestDefaultsView pre-fills for a
        fully-invested strategy portfolio — cash_balance is UNINVESTED cash)
        was accepted and made both engines crash with ZeroDivisionError. Now a
        400; the ZeroDivisionError is still reachable only by an ORM-built row.
  * V5: the estimate endpoint 500'd on malformed-but-plausible JSON. Now a 400.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.backtests.engine import rebalance_dates_for
from apps.backtests.models import Backtest
from apps.backtests.serializers import BacktestCreateSerializer
from apps.data.models import DailyBar

User = get_user_model()

START = dt.date(2023, 1, 2)


def _valid(user, **kw):
    d = {
        "name": "x", "universe": ["AAA"],
        "start_date": "2023-01-02", "end_date": "2025-12-31",
    }
    d.update(kw)
    ser = BacktestCreateSerializer(data=d, context={"request": SimpleNamespace(user=user)})
    return ser


@pytest.fixture
def user(db):
    return User.objects.create_user(email="review-val@x.test", password="pw-fake-123456789")


# ---------------------------------------------------------------------------
# V1 — step_days <= 0 is refused at the boundary AND in generate_folds.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("step", [0, -7])
def test_V1_serializer_rejects_nonpositive_step_days(user, step):
    ser = _valid(user, step_days=step)
    assert not ser.is_valid()
    assert "step_days" in ser.errors


@pytest.mark.parametrize("step", [0, -7])
def test_V1_generate_folds_raises_instead_of_spinning(step):
    """The engine-side backstop: no worker can be pinned by a row that reached
    the DB some other way. At step 0 the loop never advanced (unbounded folds
    list, 100% CPU until OOM); at step -7 `cur` marched backwards until
    datetime.date underflowed with OverflowError after ~105k folds."""
    from apps.backtests.walkforward import generate_folds

    with pytest.raises(ValueError, match="step_days must be >= 1"):
        generate_folds(
            start=dt.date(2023, 1, 2), end=dt.date(2025, 12, 31),
            is_window_days=252, oos_window_days=63, step_days=step,
        )


def test_V1_generate_folds_still_builds_normal_folds():
    from apps.backtests.walkforward import generate_folds

    folds = generate_folds(
        start=dt.date(2023, 1, 2), end=dt.date(2025, 12, 31),
        is_window_days=252, oos_window_days=63, step_days=63,
    )
    assert len(folds) > 1
    assert folds[1].oos_start >= folds[0].oos_end       # non-overlapping


# ---------------------------------------------------------------------------
# V2 — n_candidates=0 is refused; the council path can no longer IndexError.
# ---------------------------------------------------------------------------
def test_V2_serializer_rejects_zero_candidates(user):
    ser = _valid(user, n_candidates=0)
    assert not ser.is_valid()
    assert "n_candidates" in ser.errors
    # The crash it used to cause: optimize_is returns [] and run_walkforward
    # does `winner = candidates[0]`.
    from apps.backtests.optimizer import optimize_is

    stub = SimpleNamespace(
        universe=["AAA"], starting_cash=Decimal("100000"), commission_bps=Decimal("5"),
        spread_bps=Decimal("5"), rebalance_frequency="weekly", search_space={},
        is_objective="sharpe", rng_seed=42,
    )
    res = optimize_is(
        bt=stub, is_start=START, is_end=START + dt.timedelta(days=10),
        agent_outputs_cache={}, personas=["buffett"], n_candidates=0,
    )
    assert res == []


# ---------------------------------------------------------------------------
# V3 — unknown rebalance_frequency is a 400, not a silent DAILY.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("freq", ["quarterly", "Weekly", "bi-weekly", ""])
def test_V3_unknown_rebalance_frequency_is_rejected(user, freq):
    ser = _valid(user, rebalance_frequency=freq)
    assert not ser.is_valid()
    assert "rebalance_frequency" in ser.errors
    # Why it mattered: rebalance_dates_for falls through to EVERY day for an
    # unknown cadence — the most expensive one (an LLM prime per ticker-day).
    days = [START + dt.timedelta(days=i) for i in range(60)]
    days = [d for d in days if d.weekday() < 5]
    assert rebalance_dates_for(days, freq) == set(days)
    assert len(rebalance_dates_for(days, "weekly")) < len(days) / 4  # ~5x fewer


@pytest.mark.parametrize("freq", ["daily", "weekly", "monthly"])
def test_V3_known_rebalance_frequencies_still_accepted(user, freq):
    ser = _valid(user, rebalance_frequency=freq)
    assert ser.is_valid(), ser.errors
    assert ser.validated_data["rebalance_frequency"] == freq


# ---------------------------------------------------------------------------
# V4 — starting_cash=0 accepted; engine ZeroDivisionError.
# ---------------------------------------------------------------------------
def _seed(ticker: str, n: int) -> None:
    price = 100.0
    for i in range(n):
        d = START + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        price *= 1.001
        DailyBar.objects.create(
            ticker=ticker, date=d, source="fmp", open=Decimal(f"{price:.4f}"),
            high=Decimal(f"{price:.4f}"), low=Decimal(f"{price:.4f}"),
            close=Decimal(f"{price:.4f}"), adjusted_close=Decimal(f"{price:.4f}"),
            volume=1,
        )


def test_V4_zero_starting_cash_is_rejected_before_dispatch(user):
    _seed("AAA", 140)
    ser = _valid(
        user, starting_cash=0, engine_mode="risk_parity",
        end_date=(START + dt.timedelta(days=147)).isoformat(),
        is_window_days=126, oos_window_days=21, step_days=21,
    )
    assert not ser.is_valid()
    assert "starting_cash" in ser.errors
    assert not Backtest.objects.filter(user=user).exists()   # nothing dispatched

    # The same config with real cash still builds and runs to DONE.
    ok = _valid(
        user, starting_cash=100_000, engine_mode="risk_parity",
        end_date=(START + dt.timedelta(days=147)).isoformat(),
        is_window_days=126, oos_window_days=21, step_days=21,
    )
    assert ok.is_valid(), ok.errors
    bt = ok.save(user=user)
    from apps.backtests.walkforward import run_walkforward

    run_walkforward(bt)
    bt.refresh_from_db()
    assert bt.status == Backtest.DONE
    assert bt.engine_version == Backtest.ENGINE_VERSION      # stamped v2 at run time


@pytest.mark.django_db
def test_V4_zero_starting_cash_still_crashes_council_segment_math():
    """Unchanged engine behaviour, kept as the reason the boundary check exists:
    an ORM-built row with starting_cash=0 still produces an all-zero curve, so
    the serializer is the thing standing between a user and this crash."""
    from apps.backtests.engine import run_segment

    _seed("AAA", 40)
    stub = SimpleNamespace(
        universe=["AAA"], starting_cash=Decimal("0"), commission_bps=Decimal("5"),
        spread_bps=Decimal("5"), personas=None, hold_semantics="hold_existing",
        financing_bps=Decimal("200"),
    )
    seg = run_segment(
        bt=stub, start=START, end=START + dt.timedelta(days=20), pm_config={},
        agent_outputs_cache={}, rebalance_dates=set(),
    )
    assert seg.equity and all(e == 0.0 for e in seg.equity)
    with pytest.raises(ZeroDivisionError):
        # walkforward.run_walkforward line 161-163:
        _ = [(seg.equity[i] / seg.equity[i - 1]) - 1.0 for i in range(1, len(seg.equity))]


# ---------------------------------------------------------------------------
# V5 — estimate endpoint: malformed input is a 400, never a 500.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("payload", [
    {"start_date": "2024-01-01", "end_date": "2024-06-01", "universe": ["AAA"],
     "max_budget_usd": "four dollars"},                         # float() ValueError
    {"start_date": "2024-01-01", "end_date": "2024-06-01", "universe": ["AAA"],
     "model_overrides": "anthropic:claude"},                    # str.get AttributeError
    {"start_date": "2024-01-01", "end_date": "2024-06-01", "universe": ["AAA"],
     "model_overrides": {"buffett": 42}},                       # int has no split()
    {"start_date": "2024-01-01", "end_date": "2024-06-01", "universe": "AAA"},
    {"start_date": "2024-01-01", "end_date": "2024-06-01", "universe": ["AAA"],
     "rebalance_frequency": "quarterly"},
    {"start_date": "2024-01-01", "end_date": "2024-06-01", "universe": ["AAA"],
     "personas": "buffett"},
    {"start_date": "not-a-date", "end_date": "2024-06-01", "universe": ["AAA"]},
])
def test_V5_estimate_endpoint_400s_on_malformed_input(user, payload):
    c = APIClient()
    c.force_authenticate(user)
    r = c.post("/api/backtests/estimate/", payload, format="json")
    assert r.status_code == 400, r.content
    assert "detail" in r.json()


def test_V5_estimate_endpoint_reports_n_primes(user):
    """The prime phase walks unique (ticker, rebalance-day) pairs across the
    WHOLE master window — the UI needs that count, not just a dollar figure."""
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(14)]
    days = [d for d in days if d.weekday() < 5]
    for t in ("AAA", "BBB"):
        for d in days:
            DailyBar.objects.create(ticker=t, date=d, source="fmp", open=1, high=1,
                                    low=1, close=1, adjusted_close=1, volume=1)
    c = APIClient()
    c.force_authenticate(user)
    r = c.post("/api/backtests/estimate/", {
        "start_date": days[0].isoformat(), "end_date": days[-1].isoformat(),
        "universe": ["AAA", "BBB"], "rebalance_frequency": "daily",
        "personas": ["buffett"],
    }, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["n_primes"] == len(days) * 2 == body["n_invocations"]
