"""P2c improvements: hold semantics, fill-based turnover, sparse-cache abort,
provider-backed corporate actions, and the synthetic acceptance proxy.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.backtests.corporate_actions import actions_on
from apps.backtests.engine import SegmentResult
from apps.backtests.exceptions import SparseCache
from apps.backtests.models import Backtest, BacktestDay, BacktestFold
from apps.backtests.portfolio import SimulatedPortfolio
from apps.data.models import CorporateAction


# --- 1. hold_semantics ---------------------------------------------------


def test_hold_existing_skips_the_name() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000.0)
    # Seed an existing 100-share long position.
    pf._fill("AAPL", qty_delta=100, mid_price=200.0)
    cash_before = pf.cash
    qty_before = pf.positions["AAPL"].qty

    pf.execute(
        [{"ticker": "AAPL", "action": "hold", "target_weight_pct": 50.0}],
        fill_prices={"AAPL": 210.0},
        hold_semantics="hold_existing",
    )
    # Position untouched.
    assert pf.positions["AAPL"].qty == qty_before
    # No new cash movement.
    assert pf.cash == cash_before
    assert pf.fills_today == []


def test_hold_target_zero_liquidates() -> None:
    pf = SimulatedPortfolio(starting_cash=100_000.0)
    pf._fill("AAPL", qty_delta=100, mid_price=200.0)
    pf.execute(
        [{"ticker": "AAPL", "action": "hold", "target_weight_pct": 50.0}],
        fill_prices={"AAPL": 210.0},
        hold_semantics="target_zero",
    )
    assert pf.positions["AAPL"].qty == 0.0


# --- 2. Turnover from fills (not decisions) ------------------------------


@pytest.mark.django_db
def test_turnover_metric_reads_fills_not_decisions(django_user_model) -> None:
    from apps.backtests.metrics import compute_stitched_metrics

    user = django_user_model.objects.create_user(email="t@t.com", password="x" * 12)
    bt = Backtest.objects.create(
        user=user, name="t", universe=["AAPL"],
        start_date=dt.date(2024, 1, 1), end_date=dt.date(2024, 1, 5),
        starting_cash=Decimal("100000"),
    )
    fold = BacktestFold.objects.create(
        backtest=bt, fold_index=0,
        is_start=dt.date(2024, 1, 1), is_end=dt.date(2024, 1, 3),
        oos_start=dt.date(2024, 1, 4), oos_end=dt.date(2024, 1, 5),
    )
    # Day with fills present and decisions empty — the old code would have
    # computed turnover=0; the new code reads fills.
    BacktestDay.objects.create(
        backtest=bt, fold=fold, segment=BacktestDay.SEG_OOS,
        date=dt.date(2024, 1, 4),
        cash=Decimal("50000"), portfolio_value=Decimal("100000"),
        positions=[], decisions=[],
        fills=[{"ticker": "AAPL", "notional": 50000.0}],
    )
    BacktestDay.objects.create(
        backtest=bt, fold=fold, segment=BacktestDay.SEG_OOS,
        date=dt.date(2024, 1, 5),
        cash=Decimal("50000"), portfolio_value=Decimal("100000"),
        positions=[], decisions=[], fills=[],
    )
    metrics = compute_stitched_metrics(bt, [fold])
    assert float(metrics["turnover_pct"]) > 0.0, "turnover should be derived from fills"


# --- 3. Provider-backed corporate actions --------------------------------


@pytest.mark.django_db
def test_actions_on_uses_provider_rows_when_available() -> None:
    CorporateAction.objects.create(
        ticker="AAPL", as_of_date=dt.date(2020, 8, 31),
        kind=CorporateAction.SPLIT, ratio=Decimal("4"), source="fmp",
    )
    CorporateAction.objects.create(
        ticker="AAPL", as_of_date=dt.date(2020, 8, 31),
        kind=CorporateAction.CASH_DIVIDEND, amount=Decimal("0.205"), source="fmp",
    )
    out = actions_on("AAPL", dt.date(2020, 8, 31))
    kinds = {a["kind"] for a in out}
    assert kinds == {"split", "dividend"}
    split = next(a for a in out if a["kind"] == "split")
    div = next(a for a in out if a["kind"] == "dividend")
    assert split["ratio"] == 4.0
    assert div["dps"] == 0.205


# --- 5. Sparse-cache quality gate ----------------------------------------


def test_sparse_cache_exception_has_completeness() -> None:
    exc = SparseCache(completeness=0.40, required=0.85, done=40, total=100)
    assert exc.completeness == 0.40
    assert exc.required == 0.85
    # Status mapping: tasks.run_backtest maps SparseCache → ABORTED_PARTIAL.
    assert Backtest.ABORTED_PARTIAL == "aborted_partial"


# --- 7. Synthetic acceptance proxy ---------------------------------------


def test_synthetic_acceptance_proxy_executes_a_full_segment() -> None:
    """A CI-runnable smoke test for the executor that uses a fully synthetic
    decision stream — no LLM, no providers, no agent graph. Proves the
    portfolio + fills + turnover pipeline end-to-end.

    Stands in for the deferred live 3-year acceptance run while costs remain
    high; replace with the live run once a local Ollama is available.
    """
    pf = SimulatedPortfolio(starting_cash=100_000.0)
    seg = SegmentResult()
    universe = ["AAA", "BBB"]
    days = [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
    prices = {"AAA": 100.0, "BBB": 50.0}
    pending: list[dict] = []
    for day in days:
        pf.mark_to_market(prices)
        if pending:
            fills = pf.execute(pending, prices, as_of=day)
            seg.fills_by_day.append([f.__dict__ for f in fills])
        else:
            seg.fills_by_day.append([])
        # Synthetic alternating long-short rebalance.
        weight = 30.0 if day.day % 2 == 0 else 0.0
        pending = [
            {"ticker": t, "action": "buy" if weight > 0 else "sell",
             "target_weight_pct": weight}
            for t in universe
        ]
        seg.dates.append(day)
        seg.equity.append(pf.total_value)
        seg.cash.append(pf.cash)
        seg.positions_by_day.append(pf.snapshot())
        seg.decisions_by_day.append(pending)
    seg.turnover_total = pf.turnover_total_notional

    # The synthetic harness must produce: at least one fill, positive
    # turnover, and a final equity within a sane band.
    assert any(len(x) > 0 for x in seg.fills_by_day)
    assert seg.turnover_total > 0.0
    assert 50_000 < pf.total_value < 200_000
