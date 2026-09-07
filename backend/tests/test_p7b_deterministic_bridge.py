"""P7b — wire the deterministic (risk_parity) cycle into the autopilot→broker
bridge (ADR 0025).

Covers, per phase-07b §7.1:
  * the deterministic cycle reaches the shared terminal hook (the load-bearing
    wiring — ``_run_risk_parity_cycle`` now calls ``_finalize_target``);
  * the bridge BYPASSES vol-targeting (1a) and the equity per-name re-cap (1c)
    for deterministic kinds, emitting the constructed inverse-vol book verbatim
    (no de-gross, no crush to ``max_position_pct``);
  * the submit-time ``risk_check`` gate (step 3) still binds — at the per-sleeve
    cap, which a risk_parity strategy configures into ``max_position_pct``;
  * backward-compat: a risk_parity run with NO active link stays ``done`` with
    zero ``BrokerOrder``s (a non-autopilot row like #16 is byte-identical);
  * dispatch idempotency for a deterministic autopilot;
  * the paper-only invariant holds for a risk_parity link.
"""
from __future__ import annotations

import datetime as dt
import types
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.brokers import demo_fills
from apps.brokers.adapters import mock as mock_adapter
from apps.brokers.models import BrokerAccount, BrokerOrder, StrategyBrokerLink
from apps.portfolios import autopilot as bridge
from apps.portfolios import autopilot_risk, regime_scaling, tasks, tasks_autopilot
from apps.portfolios.models import (
    AutopilotRun,
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    RebalanceOrder,
    StrategyAutopilot,
    Universe,
    UniverseMembership,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_mock():
    mock_adapter.reset_state()
    yield
    mock_adapter.reset_state()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="p7b@x.test", password="pw-fake-123456789")


_SLEEVES = (("SPY", "Equity"), ("TLT", "Rates"), ("GLD", "Commodity"), ("DBC", "Commodity"))


def _rp_universe():
    u = Universe.objects.create(name="p7b-cross-asset")
    for t, sec in _SLEEVES:
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector=sec, effective_from=dt.date(2007, 1, 1),
        )
    return u


def _rp_strategy(user, **kw):
    """A cross-asset risk_parity strategy with the §4 pinned params — crucially
    ``max_position_pct = per_etf_max_pct`` so the submit-gate's binding cap is the
    per-sleeve cap, not the (irrelevant here) equity default 0.03."""
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name="rpbook",
        cash_balance=Decimal("100000"),
    )
    defaults = dict(
        kind=PortfolioStrategy.KIND_RISK_PARITY,
        target_gross_pct=Decimal("1.00"),
        per_etf_max_pct=Decimal("0.50"),
        per_etf_min_pct=Decimal("0.02"),
        rebalance_band_pct=Decimal("0.05"),
        max_position_pct=Decimal("0.50"),   # gate cap == per-sleeve cap (Part C)
        min_trade_notional_usd=Decimal("100"),
        max_turnover_pct=Decimal("5.0"),    # high so the cash→full deploy never clips
        personas=[],
    )
    defaults.update(kw)
    return PortfolioStrategy.objects.create(
        user=user, name="Cross-Asset Risk Parity", universe=_rp_universe(),
        portfolio=pf, **defaults,
    )


def _broker_account(user, *, broker="mock", mode=BrokerAccount.MODE_PAPER, cash="100000"):
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_BROKER, name="bk", cash_balance=Decimal(cash),
    )
    acc = BrokerAccount.objects.create(
        user=user, broker=broker, mode=mode, account_id=f"{broker}-{user.id}",
        label="L", portfolio=pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    if broker == "mock":
        mock_adapter.seed_demo_book(acc, cash=Decimal(cash))
    return acc


def _linked_rp(user, account, *, enabled=True, state=StrategyAutopilot.STATE_ACTIVE, **skw):
    strategy = _rp_strategy(user, **skw)
    StrategyBrokerLink.objects.create(strategy=strategy, broker_account=account)
    ap = StrategyAutopilot.objects.create(
        strategy=strategy, broker_account=account, is_enabled=enabled, state=state,
        # Effectively unlimited daily order/notional caps, so these unit tests
        # isolate the bypass + risk gate; the default $50k/day cap would
        # otherwise throttle a full $100k deploy (a real Part B/F config note,
        # not under test here). NB: 0 means *zero* — a hard stop, not "off".
        max_orders_per_day=1000, max_notional_per_day_usd=Decimal("100000000"),
    )
    return strategy, ap


def _rp_target(strategy, weights, *, price=100):
    """A DONE risk_parity target carrying a constructed inverse-vol book, with the
    cycle's RebalanceOrders as the price seed for the broker-book recompute."""
    target = PortfolioTarget.objects.create(
        strategy=strategy, as_of_date=dt.date(2026, 6, 5),
        status=PortfolioTarget.DONE, target_weights=weights,
    )
    for i, t in enumerate(weights):
        RebalanceOrder.objects.create(
            target=target, ticker=t, side="buy", quantity=Decimal("1"),
            limit_price=Decimal(str(price)), reason="open",
            estimated_notional_usd=Decimal(str(price)), sequence=i,
        )
    return target


# ---------------------------------------------------------------------------
# Bypass (ADR 0025 §2): deterministic kinds skip vol-targeting + the equity
# per-name re-cap; the constructed book is emitted verbatim.
# ---------------------------------------------------------------------------
def test_deterministic_bridge_skips_vol_target_and_cap_reapply(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("100"))
    called = {"vol": False, "caps": False}

    def _record_vol(*a, **k):
        called["vol"] = True
        return 0.5, {"scale": 0.5}            # would HALVE an equity book

    def _record_caps(weights, strategy, sector_of=None):
        called["caps"] = True
        return {t: 0.04 for t in weights}, []  # would crush every sleeve to 4%

    monkeypatch.setattr(autopilot_risk, "vol_target_scale", _record_vol)
    monkeypatch.setattr(autopilot_risk, "apply_caps", _record_caps)

    account = _broker_account(user, broker="mock")
    strategy, ap = _linked_rp(user, account)
    # A skewed inverse-vol book; every sleeve is far above the equity 0.03 default.
    target = _rp_target(strategy, {"TLT": 0.40, "GLD": 0.30, "SPY": 0.20, "DBC": 0.10})

    decision = bridge.maybe_emit_and_submit(
        target, link=strategy.broker_links.first(), autopilot=ap,
    )

    # The bypass guards held — neither council pre-processor ran.
    assert called["vol"] is False
    assert called["caps"] is False
    assert decision["submitted"] == 4

    # The emitted book matches target_weights × NAV / price (no de-gross to 0.5,
    # no crush to 0.04): at $100/sh on a $100k book → 400/300/200/100 shares.
    qty = {o.ticker: o.quantity for o in BrokerOrder.objects.filter(broker_account=account)}
    assert qty == {
        "TLT": Decimal("400"), "GLD": Decimal("300"),
        "SPY": Decimal("200"), "DBC": Decimal("100"),
    }
    # Full gross deployed (~100k), not 16% (caps) or 50% (vol-target).
    invested = sum(q * Decimal("100") for q in qty.values())
    assert invested == Decimal("100000")


# ---------------------------------------------------------------------------
# Step 3 still binds: an order over the per-sleeve cap is rejected by the gate,
# while a compliant sleeve in the same cycle goes through.
# ---------------------------------------------------------------------------
def test_deterministic_bridge_gate_rejects_oversized_sleeve(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("100"))
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked_rp(user, account)
    # A single sleeve at 60% breaches the 50% per-sleeve cap (max_position_pct) on
    # a fresh book (unambiguous NAV) — the submit-time risk gate must reject it,
    # proving step 3 stays ON for deterministic kinds (only 1a/1c are bypassed).
    target = _rp_target(strategy, {"TLT": 0.60})

    decision = bridge.maybe_emit_and_submit(
        target, link=strategy.broker_links.first(), autopilot=ap,
    )

    items = {i["ticker"]: i for i in decision["items"]}
    assert "rejected" in items["TLT"]
    assert decision["submitted"] == 0
    assert BrokerOrder.objects.filter(
        broker_account=account, status=BrokerOrder.STATUS_FILLED,
    ).count() == 0


def test_risk_check_binds_at_configured_per_sleeve_cap(user):
    account = _broker_account(user, broker="alpaca_paper")
    strategy = _rp_strategy(user)  # max_position_pct = 0.50
    check = autopilot_risk.make_risk_check(strategy)

    ok = BrokerOrder.objects.create(
        broker_account=account, ticker="TLT", side="buy",
        quantity=Decimal("400"), order_type="market", limit_price=Decimal("100"),
    )  # $40k = 40% of $100k < 50% cap
    assert check(ok) == []

    big = BrokerOrder.objects.create(
        broker_account=account, ticker="TLT", side="buy",
        quantity=Decimal("600"), order_type="market", limit_price=Decimal("100"),
    )  # $60k = 60% > 50% cap
    assert check(big)


# ---------------------------------------------------------------------------
# The wiring itself (ADR 0025 §1): the deterministic cycle reaches the hook.
# ---------------------------------------------------------------------------
def test_risk_parity_cycle_invokes_bridge_hook(user, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        bridge, "_finalize_target",
        lambda target: seen.setdefault("target_id", target.pk),
    )
    monkeypatch.setattr(
        regime_scaling, "regime_gate_excluded_sleeves",
        lambda strategy, members, as_of=None: {},
    )
    monkeypatch.setattr(
        tasks, "compute_vols_for",
        lambda tickers, as_of, window_days, data_provider: {
            "SPY": types.SimpleNamespace(daily_vol=0.012),
            "TLT": types.SimpleNamespace(daily_vol=0.008),
            "GLD": types.SimpleNamespace(daily_vol=0.010),
        },
    )

    class _Bar:
        def __init__(self, c):
            self.close = c

    class _Prov:
        def get_daily_bars(self, t, start=None, end=None, as_of=None):
            return [_Bar({"SPY": 500.0, "TLT": 90.0, "GLD": 180.0}.get(t, 100.0))]

    monkeypatch.setattr(tasks, "get_fmp_provider", lambda user=None: _Prov())

    strategy = _rp_strategy(user)
    members = [("SPY", "Equity"), ("TLT", "Rates"), ("GLD", "Commodity")]
    res = tasks._run_risk_parity_cycle(strategy, dt.date(2024, 6, 3), members)

    assert res["status"] == "done"
    assert seen.get("target_id") == res["target_id"]  # the hook saw the persisted target


# ---------------------------------------------------------------------------
# Backward-compat: a risk_parity run with no active link is a byte-identical
# no-op (the non-autopilot #16 path is unchanged).
# ---------------------------------------------------------------------------
def test_risk_parity_no_link_is_noop(user):
    strategy = _rp_strategy(user)  # no link, no autopilot
    target = _rp_target(strategy, {"TLT": 0.5, "GLD": 0.5})
    assert bridge._finalize_target(target) is None
    target.refresh_from_db()
    assert target.status == PortfolioTarget.DONE
    assert BrokerOrder.objects.count() == 0


def test_risk_parity_disabled_autopilot_is_noop(user):
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked_rp(user, account, enabled=False)
    target = _rp_target(strategy, {"TLT": 0.5, "GLD": 0.5})
    assert bridge._finalize_target(target) is None
    target.refresh_from_db()
    assert target.status == PortfolioTarget.DONE
    assert BrokerOrder.objects.count() == 0


# ---------------------------------------------------------------------------
# Dispatch due-window: a deterministic autopilot fires once per due window (it
# advances next_run_at, so an immediate re-poll is not due). Emission-level
# idempotency — re-firing the SAME (autopilot, fire_time) — is enforced by the
# shared dispatcher's unique constraint + the deterministic client_order_id, and
# is unchanged by this diff (see test_p7_autopilot_bridge::test_autopilot_run_fire_time_unique).
# ---------------------------------------------------------------------------
def test_risk_parity_dispatch_fires_once_per_due_window(user, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        tasks_autopilot.run_autopilot_cycle, "delay", lambda rid: calls.append(rid),
    )
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked_rp(user, account)
    ap.is_market_aware = False
    ap.next_run_at = timezone.now() - dt.timedelta(minutes=1)  # due; advances to next week
    ap.save(update_fields=["is_market_aware", "next_run_at"])

    tasks_autopilot.dispatch_due_autopilots()  # due → fires + advances next_run_at
    tasks_autopilot.dispatch_due_autopilots()  # no longer due → no second fire

    assert len(calls) == 1
    assert AutopilotRun.objects.filter(autopilot=ap).count() == 1


# ---------------------------------------------------------------------------
# Non-regression twin of the bypass: a COUNCIL kind STILL runs 1a (vol-target)
# and 1c (apply_caps). Guards against a future edit that broadens
# DETERMINISTIC_KINDS or inverts the `if not deterministic:` guard.
# ---------------------------------------------------------------------------
def test_council_kind_still_runs_vol_target_and_caps(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("100"))
    called = {"vol": False, "caps": False}

    def _record_vol(*a, **k):
        called["vol"] = True
        return 1.0, {"scale": 1.0}

    def _record_caps(weights, strategy, sector_of=None):
        called["caps"] = True
        return weights, []

    monkeypatch.setattr(autopilot_risk, "vol_target_scale", _record_vol)
    monkeypatch.setattr(autopilot_risk, "apply_caps", _record_caps)

    account = _broker_account(user, broker="mock")
    strategy, ap = _linked_rp(user, account, kind=PortfolioStrategy.KIND_LONG_SHORT)
    target = _rp_target(strategy, {"TLT": 0.04, "GLD": 0.04})

    bridge.maybe_emit_and_submit(target, link=strategy.broker_links.first(), autopilot=ap)

    assert called["vol"] is True   # 1a still runs for council kinds
    assert called["caps"] is True  # 1c still runs for council kinds


# ---------------------------------------------------------------------------
# Book-of-record: a deterministic cycle that was within_band on the STRATEGY
# book must still rebalance the BROKER book (the bridge ignores cycle_outcome and
# recomputes the broker delta) — the band-free weekly backtest demands it.
# ---------------------------------------------------------------------------
def test_within_band_flag_does_not_suppress_broker_rebalance(user, monkeypatch):
    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("100"))
    account = _broker_account(user, broker="mock")
    strategy, ap = _linked_rp(user, account)
    target = _rp_target(strategy, {"TLT": 0.5, "GLD": 0.5})
    target.cycle_outcome = "within_rebalance_band"  # strategy book said "don't trade"
    target.save(update_fields=["cycle_outcome"])

    decision = bridge.maybe_emit_and_submit(
        target, link=strategy.broker_links.first(), autopilot=ap,
    )

    # The empty broker book is still fully deployed — the band flag is ignored.
    assert decision["submitted"] == 2


# ---------------------------------------------------------------------------
# Defense-in-depth: a deterministic strategy whose max_position_pct is below the
# per-sleeve cap (the silent-no-trade misconfig) is surfaced in the run audit.
# ---------------------------------------------------------------------------
def test_cap_misconfig_warns_and_rejects(user, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(demo_fills, "live_price", lambda account, ticker: Decimal("100"))
    account = _broker_account(user, broker="mock")
    # max_position_pct (0.03) < per_etf_max_pct (0.50) → the gate would reject the
    # inverse-vol sleeves; the bridge must flag it rather than fail silently.
    strategy, ap = _linked_rp(user, account, max_position_pct=Decimal("0.03"))
    target = _rp_target(strategy, {"TLT": 0.30, "GLD": 0.30})

    with caplog.at_level(logging.WARNING, logger="apps.portfolios.autopilot"):
        decision = bridge.maybe_emit_and_submit(
            target, link=strategy.broker_links.first(), autopilot=ap,
        )

    assert "silent no-trade" in caplog.text                # surfaced, not silent
    assert decision["submitted"] == 0                      # fail-safe: no wrong trades
    assert all("rejected" in i for i in decision["items"])  # every sleeve gate-rejected


# ---------------------------------------------------------------------------
# Paper-only invariant: a risk_parity link to a LIVE account is rejected.
# ---------------------------------------------------------------------------
def test_risk_parity_link_rejects_live_account(user):
    live = _broker_account(user, broker="alpaca_paper", mode=BrokerAccount.MODE_LIVE)
    strategy = _rp_strategy(user)
    with pytest.raises(ValidationError):
        StrategyBrokerLink(strategy=strategy, broker_account=live).save()
