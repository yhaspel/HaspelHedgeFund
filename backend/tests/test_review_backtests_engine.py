"""Adversarial review (reviewer: backtests) — engine / metrics math proofs.

FIXED by WP B3a (engine v2). Each test now asserts the corrected behaviour;
the bug each one used to prove is described in the past tense.

  * E1: financing drag was charged only on ``max(0, -cash)``. A 100/100
        long/short book (gross 2.0) holds POSITIVE cash (short proceeds) and was
        charged $0/yr at financing_bps=200 — the short leg's borrow fee and the
        paper's (L-1)*rf carry were both missing; a short-only "crisis sleeve"
        book ran free too. v2 charges ``financing_bps + short_borrow_bps`` on
        |short notional| on top of the margin debit.
  * E2: the corporate-action fallback inferred a SPLIT from the raw close ratio
        only (``close_prev / close_today``) and never consulted adjusted_close,
        so a genuine -50% (or -33%, -90%, +100%) crash day on a ticker without a
        CorporateAction row was booked as a split — qty rescaled, loss erased.
        v2 reads the ADJUST FACTOR (adjusted_close/close) jump, which is flat
        across a real price move and equals the ratio across a real split.
  * E3: the council walk-forward started a FRESH SimulatedPortfolio per OOS
        fold, so the market move over each fold's last session was dropped from
        the stitched curve and the whole book was re-bought at the next fold's
        first fill. v2 carries one book across folds on BOTH paths and marks it
        to the prior session's close at each boundary.
  * E4: ``sharpe_deflation = mean_oos / mean_is`` was sign-blind: a strategy
        WORSE out of sample than in sample (both negative) reported a ratio > 1
        and ``red_flag=False``. v2 divides by ``abs(mean_is)``.
  * E5: ``data_era`` is stamped ``total_return`` for every new row regardless
        of whether CorporateAction dividend rows exist for the universe — the
        engine credits dividends only from those rows (no scheduled backfill).
        STILL OPEN — out of scope for B3a (needs a dividend backfill job).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.backtests.corporate_actions import actions_on, apply_actions
from apps.backtests.engine import rebalance_dates_for, run_segment, trading_days
from apps.backtests.metrics import compute_stitched_metrics
from apps.backtests.models import Backtest, BacktestDay, BacktestFold
from apps.backtests.portfolio import SimulatedPortfolio
from apps.data.models import CorporateAction, DailyBar


# ---------------------------------------------------------------------------
# E1 — financing on short legs.
# ---------------------------------------------------------------------------
def test_E1_market_neutral_gross_2x_pays_short_financing():
    """A 100/100 long-short book pays borrow + financing on the short leg even
    though the short proceeds leave cash positive."""
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0,
                            financing_bps=200, short_borrow_bps=50)
    pf.execute(
        [
            {"ticker": "L", "action": "buy", "target_weight_pct": 100.0},
            {"ticker": "S", "action": "open_short", "target_weight_pct": -100.0},
        ],
        fill_prices={"L": 100.0, "S": 100.0}, max_gross=2.0,
    )
    gross = sum(abs(p.market_value) for p in pf.positions.values())
    assert gross == pytest.approx(200_000, rel=1e-6)       # gross 2.0 (L=2)
    assert pf.cash == pytest.approx(100_000, abs=1.0)      # short proceeds sit in cash
    one_day = pf.accrue_financing()
    # 100k short notional × (200 + 50)bps / 252.
    assert one_day == pytest.approx(100_000 * 0.025 / 252, rel=1e-9)
    total = one_day + sum(pf.accrue_financing() for _ in range(251))
    assert total == pytest.approx(100_000 * 0.025, rel=1e-9)   # 2.5%/yr on the short leg
    assert pf.cash == pytest.approx(100_000 - total, rel=1e-9)


def test_E1_short_only_book_pays_borrow_financing():
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0,
                            financing_bps=200, short_borrow_bps=50)
    pf.execute(
        [{"ticker": "S", "action": "open_short", "target_weight_pct": -100.0}],
        fill_prices={"S": 50.0},
    )
    assert pf.positions["S"].qty < 0
    assert pf.cash == pytest.approx(200_000, abs=1.0)
    # 100k short notional at 2.5%/yr — borrow fee + rebate spread, not $0.
    assert pf.accrue_financing() == pytest.approx(100_000 * 0.025 / 252, rel=1e-9)


def test_E1_short_borrow_rate_defaults_from_settings(settings):
    """The borrow rate is settings-driven (BACKTEST_SHORT_BORROW_BPS, default 50)
    so a hard-to-borrow regime can be modelled without a code change."""
    settings.BACKTEST_SHORT_BORROW_BPS = 400
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0,
                            financing_bps=200)
    assert pf.short_borrow_bps == 400
    pf.execute(
        [{"ticker": "S", "action": "open_short", "target_weight_pct": -100.0}],
        fill_prices={"S": 50.0},
    )
    assert pf.accrue_financing() == pytest.approx(100_000 * 0.06 / 252, rel=1e-9)


def test_E1_financing_bps_zero_is_still_the_master_off_switch():
    """Callers that never opted into financing (the dataclass default) are
    unchanged — no surprise borrow charge appears in their results."""
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0,
                            financing_bps=0)
    pf.execute(
        [{"ticker": "S", "action": "open_short", "target_weight_pct": -100.0}],
        fill_prices={"S": 50.0},
    )
    assert pf.positions["S"].qty < 0
    assert pf.accrue_financing() == 0.0


def test_E1_long_levered_book_is_charged_as_documented():
    """Control: the long-levered case the code targets does charge (L-1)*rf."""
    pf = SimulatedPortfolio(starting_cash=100_000, commission_bps=0, spread_bps=0,
                            financing_bps=200)
    pf.execute(
        [{"ticker": "L", "action": "buy", "target_weight_pct": 200.0}],
        fill_prices={"L": 100.0}, max_gross=2.0,
    )
    assert pf.cash == pytest.approx(-100_000, abs=1.0)
    assert pf.accrue_financing() == pytest.approx(100_000 * 0.02 / 252, rel=1e-6)


# ---------------------------------------------------------------------------
# E2 — crash day booked as a split.
# ---------------------------------------------------------------------------
def _bar(ticker, date, close, adj, source="fmp"):
    DailyBar.objects.create(
        ticker=ticker, date=date, source=source, open=Decimal(str(close)),
        high=Decimal(str(close)), low=Decimal(str(close)),
        close=Decimal(str(close)), adjusted_close=Decimal(str(adj)), volume=1,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("prev_close,today_close", [
    (100.0, 50.0),       # -50% day (both close AND adjusted_close halve)
    (100.0, 66.9),       # -33.1% day
    (100.0, 10.2),       # -89.8% day (biotech/SPAC blow-up)
    (100.0, 199.0),      # +99% day (takeover) — used to book a REVERSE split
])
def test_E2_crash_day_is_not_a_split_and_the_pnl_lands(prev_close, today_close):
    d0, d1 = dt.date(2024, 3, 1), dt.date(2024, 3, 4)
    # adjusted_close moves 1:1 with close — the signature of a real price move,
    # NOT a split (a split leaves adjusted_close continuous). The adjust factor
    # is 1.0 on both days, so v2 infers nothing.
    _bar("CRSH", d0, prev_close, prev_close)
    _bar("CRSH", d1, today_close, today_close)
    assert not CorporateAction.objects.filter(ticker="CRSH").exists()

    acts = actions_on("CRSH", d1)
    assert acts == []

    # Engine effect: hold 100 shares bought at prev_close; the crash is now the
    # full (today/prev - 1) hit to equity instead of being rescaled away.
    pf = SimulatedPortfolio(starting_cash=0.0, commission_bps=0, spread_bps=0)
    pf.positions["CRSH"] = pf.positions.get("CRSH") or __import__(
        "apps.backtests.portfolio", fromlist=["Position"]
    ).Position(ticker="CRSH", qty=100.0, avg_cost=prev_close, mark=prev_close)
    before = pf.total_value
    apply_actions(pf, "CRSH", acts)
    pf.mark_to_market({"CRSH": today_close})
    after = pf.total_value
    true_move = today_close / prev_close - 1.0
    assert abs(true_move) > 0.30                     # the real move was >30%...
    assert after / before - 1.0 == pytest.approx(true_move, rel=1e-9)  # ...and it lands


@pytest.mark.django_db
@pytest.mark.parametrize("prev_close,prev_adj,today_close,expected_ratio", [
    (100.0, 50.0, 50.0, 2.0),     # 2:1 — back-adjusted history, adj continuous
    (400.0, 100.0, 100.0, 4.0),   # 4:1
    (100.0, 10.0, 10.0, 10.0),    # 10:1
    (50.0, 100.0, 100.0, 0.5),    # 1:2 reverse split
])
def test_E2_control_genuine_split_is_still_detected(
    prev_close, prev_adj, today_close, expected_ratio
):
    d0, d1 = dt.date(2024, 3, 1), dt.date(2024, 3, 4)
    _bar("SPLT", d0, prev_close, prev_adj)   # adjusted_close continuous...
    _bar("SPLT", d1, today_close, today_close)  # ...across the close jump
    assert actions_on("SPLT", d1) == [{"kind": "split", "ratio": expected_ratio}]
    # A real split and a real crash are now distinguishable: the adjust factor
    # (adjusted_close/close) jumps by the ratio for the split and stays flat for
    # the crash, even though the raw close ratio is identical (100 -> 50).


# ---------------------------------------------------------------------------
# E3 — council path: fresh book per fold.
# ---------------------------------------------------------------------------
START = dt.date(2024, 1, 1)


def _seed_flat_then_move(ticker: str, n_cal: int, jump_on: dt.date, jump: float) -> None:
    """Flat 100 except a single +jump% move on `jump_on` (close), open == prior close."""
    price = 100.0
    for i in range(n_cal):
        d = START + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        open_ = price
        if d == jump_on:
            price *= 1.0 + jump
        DailyBar.objects.create(
            ticker=ticker, date=d, source="fmp", open=Decimal(f"{open_:.4f}"),
            high=Decimal(f"{max(open_, price):.4f}"), low=Decimal(f"{min(open_, price):.4f}"),
            close=Decimal(f"{price:.4f}"), adjusted_close=Decimal(f"{price:.4f}"), volume=1,
        )


def _all_bullish(universe, days):
    return {
        (t, d): {
            "buffett": {"signal": "bullish", "confidence": 90, "thesis": ""},
            "risk": {"veto": False, "max_position_pct_for_this_trade": 0.5,
                     "hard_caps_applied": []},
            "valuation": {"current_price": 100.0},
        }
        for t in universe for d in days
    }


@pytest.mark.django_db
def test_E3_council_fold_boundary_carries_the_book_and_stitches_continuously():
    universe = ["AAA"]
    # Fold A = Mon 2024-01-08 .. Fri 2024-01-12 ; Fold B = Mon 01-15 .. Fri 01-19.
    # The ONLY price move is +10% at the close of Fold A's last day (01-12).
    _seed_flat_then_move("AAA", 30, jump_on=dt.date(2024, 1, 12), jump=0.10)
    a0, a1 = dt.date(2024, 1, 8), dt.date(2024, 1, 12)
    b0, b1 = dt.date(2024, 1, 15), dt.date(2024, 1, 19)
    days = trading_days(a0, b1, universe)
    cache = _all_bullish(universe, days)
    bt = SimpleNamespace(
        universe=universe, starting_cash=Decimal("100000"), commission_bps=Decimal("0"),
        spread_bps=Decimal("0"), personas=["buffett"], hold_semantics="hold_existing",
        financing_bps=Decimal("0"),
    )
    pm = {"buy_threshold": 0.1, "sell_threshold": -0.1, "min_confidence": 0.5,
          "max_weight": 0.5, "vol_target_annual": None, "weights": {"buffett": 1.0}}

    # Engine v2: the walk-forward hands ONE book to every OOS fold in turn.
    pf = SimulatedPortfolio(starting_cash=100_000.0, commission_bps=0.0, spread_bps=0.0)
    seg_a = run_segment(bt=bt, start=a0, end=a1, pm_config=pm, agent_outputs_cache=cache,
                        rebalance_dates=rebalance_dates_for(trading_days(a0, a1, universe),
                                                            "daily"),
                        pf=pf)
    seg_b = run_segment(bt=bt, start=b0, end=b1, pm_config=pm, agent_outputs_cache=cache,
                        rebalance_dates=rebalance_dates_for(trading_days(b0, b1, universe),
                                                            "daily"),
                        pf=pf)
    whole = run_segment(bt=bt, start=a0, end=b1, pm_config=pm, agent_outputs_cache=cache,
                        rebalance_dates=rebalance_dates_for(days, "daily"))

    assert any(p for p in seg_a.positions_by_day)          # fold A actually holds AAA
    a_last_book = sum(abs(p["market_value"]) for p in seg_a.positions_by_day[-1])
    assert a_last_book >= 0.40 * 100_000                   # ~45% invested at the boundary
    # (1) Fold A's snapshot still lags one session (its last close is marked on
    #     the NEXT session) — that is the segment's own boundary, not a loss.
    assert seg_a.equity[-1] == pytest.approx(100_000.0, rel=1e-9)
    # (2) Fold B opens on the carried book, marked to 01-12's close: the +10%
    #     lands as B's first equity point instead of vanishing.
    assert seg_b.equity[0] / seg_a.equity[-1] > 1.04
    # (3) The continuous stitch over both folds equals a single unbroken run.
    stitched_ratio = seg_b.equity[-1] / seg_a.equity[0]
    assert whole.equity[-1] / whole.equity[0] > 1.04
    assert stitched_ratio == pytest.approx(whole.equity[-1] / whole.equity[0], rel=1e-6)
    # (4) No phantom turnover at the boundary: fold B never re-buys the book, it
    #     only trims back to target after the mark (was $45,000 of round-trip).
    first_fills = next((f for f in seg_b.fills_by_day if f), [])
    rebuy = sum(abs(x["notional"]) for x in first_fills)
    assert rebuy < 0.10 * 100_000


# ---------------------------------------------------------------------------
# E4 — deflation ratio sign-blind.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_E4_negative_is_and_worse_oos_is_red_flagged(django_user_model):
    u = django_user_model.objects.create_user(email="e4@x.test", password="pw-fake-123456789")
    bt = Backtest.objects.create(
        user=u, name="e4", universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=400), status=Backtest.DONE,
        engine_mode=Backtest.COUNCIL, n_candidates=50,
    )
    f = BacktestFold.objects.create(
        backtest=bt, fold_index=0, is_start=START, is_end=START, oos_start=START,
        oos_end=START, is_sharpe=Decimal("-0.5"), oos_sharpe=Decimal("-2.0"),
    )
    BacktestDay.objects.create(backtest=bt, fold=f, segment="oos", date=START, cash=0,
                               positions=[], portfolio_value=Decimal("100000"))
    m = compute_stitched_metrics(bt, [f], None)
    # -2.0 / |-0.5| => -4.0: the haircut keeps the OOS Sharpe's sign, so a
    # negative OOS can never read as "4x better out of sample".
    assert m["sharpe_deflation"] == Decimal("-4.0")
    from rest_framework.test import APIClient

    from apps.backtests.models import BacktestMetrics

    BacktestMetrics.objects.create(backtest=bt, **{k: v for k, v in m.items()
                                                    if k != "benchmarks"})
    c = APIClient()
    c.force_authenticate(u)
    body = c.get(f"/api/backtests/{bt.id}/deflation/").json()
    assert body["deflation_meaningful"] is True
    assert body["sharpe_deflation"] == -4.0               # serializer does not invert it
    assert body["red_flag"] is True                       # OOS << IS is flagged


@pytest.mark.django_db
def test_E4_positive_case_keeps_the_documented_haircut(django_user_model):
    """Control: the ordinary IS>0 / OOS>0 case is untouched — mean_is is positive
    so abs() is a no-op and the ratio still reads as "OOS kept 80% of IS"."""
    u = django_user_model.objects.create_user(email="e4b@x.test", password="pw-fake-123456789")
    bt = Backtest.objects.create(
        user=u, name="e4b", universe=["AAA"], start_date=START,
        end_date=START + dt.timedelta(days=400), status=Backtest.DONE,
        engine_mode=Backtest.COUNCIL, n_candidates=50,
    )
    f = BacktestFold.objects.create(
        backtest=bt, fold_index=0, is_start=START, is_end=START, oos_start=START,
        oos_end=START, is_sharpe=Decimal("1.0"), oos_sharpe=Decimal("0.8"),
    )
    BacktestDay.objects.create(backtest=bt, fold=f, segment="oos", date=START, cash=0,
                               positions=[], portfolio_value=Decimal("100000"))
    m = compute_stitched_metrics(bt, [f], None)
    assert m["sharpe_deflation"] == Decimal("0.8")


# ---------------------------------------------------------------------------
# E5 — data_era claims total-return without dividend rows.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_E5_new_backtest_is_total_return_era_even_with_no_dividend_rows(django_user_model):
    u = django_user_model.objects.create_user(email="e5@x.test", password="pw-fake-123456789")
    assert not CorporateAction.objects.exists()
    bt = Backtest.objects.create(
        user=u, name="e5", universe=["TLT"], start_date=START,
        end_date=START + dt.timedelta(days=400),
    )
    assert bt.data_era == Backtest.ERA_TOTAL_RETURN
    # engine side: with no CorporateAction rows, actions_on() credits no cash dividend
    _bar("TLT", START, 100.0, 100.0)
    _bar("TLT", START + dt.timedelta(days=1), 100.0, 100.0)
    assert actions_on("TLT", START + dt.timedelta(days=1)) == []


# ---------------------------------------------------------------------------
# E6 — overlapping OOS folds (step_days < oos_window_days) corrupt the stitch.
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_E6_overlapping_oos_folds_break_stitched_curve(django_user_model):
    """generate_folds still *can* produce overlap (step < oos), and
    _stitched_oos_equity cannot represent it: it orders BacktestDay by date only
    and restarts the chain every time fold_id changes, so interleaved rows from
    two folds flat-line the curve. FIXED at the create boundary — the serializer
    now refuses step_days < oos_window_days (asserted below), so no new run can
    reach this shape. The rest of this test pins the corruption the guard
    prevents."""
    from types import SimpleNamespace as _NS

    from apps.backtests.metrics import stitched_oos_returns
    from apps.backtests.serializers import BacktestCreateSerializer
    from apps.backtests.walkforward import generate_folds

    folds = generate_folds(start=START, end=START + dt.timedelta(days=400),
                           is_window_days=126, oos_window_days=63, step_days=21)
    assert folds[1].oos_start < folds[0].oos_end          # overlap is generated

    guard_user = django_user_model.objects.create_user(
        email="e6-guard@x.test", password="pw-fake-123456789"
    )
    ser = BacktestCreateSerializer(
        data={"name": "overlap", "universe": ["AAA"], "start_date": START.isoformat(),
              "end_date": (START + dt.timedelta(days=400)).isoformat(),
              "is_window_days": 126, "oos_window_days": 63, "step_days": 21},
        context={"request": _NS(user=guard_user)},
    )
    assert not ser.is_valid()
    assert "overlapping OOS folds" in str(ser.errors)

    u = django_user_model.objects.create_user(email="e6@x.test", password="pw-fake-123456789")
    bt = Backtest.objects.create(user=u, name="e6", universe=["AAA"], start_date=START,
                                 end_date=START + dt.timedelta(days=400), status=Backtest.DONE)
    fa = BacktestFold.objects.create(backtest=bt, fold_index=0, is_start=START, is_end=START,
                                     oos_start=START, oos_end=START + dt.timedelta(days=5))
    fb = BacktestFold.objects.create(backtest=bt, fold_index=1, is_start=START, is_end=START,
                                     oos_start=START + dt.timedelta(days=3),
                                     oos_end=START + dt.timedelta(days=8))
    # Both folds: +1%/day books. Fold A days 0..5, fold B days 3..8 (overlap 3..5).
    v = 100_000.0
    for i in range(0, 6):
        BacktestDay.objects.create(backtest=bt, fold=fa, segment="oos",
                                   date=START + dt.timedelta(days=i), cash=0, positions=[],
                                   portfolio_value=Decimal(str(round(v * 1.01 ** i, 2))))
    for i in range(3, 9):
        BacktestDay.objects.create(backtest=bt, fold=fb, segment="oos",
                                   date=START + dt.timedelta(days=i), cash=0, positions=[],
                                   portfolio_value=Decimal(str(round(v * 1.01 ** (i - 3), 2))))
    dates, equity, rets = stitched_oos_returns(bt)
    assert len(dates) == 12 and len(set(dates)) == 9        # duplicated dates in the curve
    # Every book compounds +1%/day over 8 days => a faithful stitch is ~+8.3%.
    # The interleaved chain restarts at every fold flip and never compounds the
    # overlap window.
    total = equity[-1] / equity[0] - 1.0
    faithful = 1.01 ** 8 - 1.0                              # 8.29%
    assert total == pytest.approx(0.0615, abs=1e-3)          # 3 of 8 up-days lost
    assert total < faithful - 0.02
    assert sum(1 for r in rets if abs(r) < 1e-12) == 5      # phantom zero-return steps
