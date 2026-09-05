"""P7 — the autonomous strategy→broker bridge (apps/portfolios).

``_finalize_target`` is the shared terminal hook called from ``finalize_cycle``
after the cycle's ``RebalanceOrder`` rows exist. For a broker-linked strategy
whose autopilot is enabled and not halted, it runs ``maybe_emit_and_submit``,
which:

  1. vol-scales + caps the cycle's target weights (``autopilot_risk``) — the demo
     path is covered here because it skips ``confirmation.gate``;
  2. recomputes the rebalance against the strategy's **real book of record**
     (``rebalance.compute_orders``) so we trade the delta, not the full target.
     P14: for a fund member that book is its SLEEVE — its attributed slice of
     the fund's shared broker account (``sleeves.execution_context``) — so N
     strategies size against their own capital and never touch each other's
     holdings; a legacy strategy linked to an account of its own still uses the
     whole account book;
  3. for each resulting order: creates a ``BrokerOrder`` with a deterministic
     ``client_order_id = rbo-<rebalance_order_id>`` (overriding the UUID4
     default) tagged with the sleeve, holds it ``pending_open`` if the market is
     closed, otherwise gates it (``risk_check`` wired in) and submits
     idempotently — honoring the daily order/notional caps;
  4. marks the target ``AUTOPILOT_SUBMITTED`` (terminal, out of ACTIVE_STATUSES).

Paper-only and autonomous: the gate hard-blocks live × scheduled_job, and there
is no human review step. Backward-compatible: a strategy with no sleeve and no
active link is a no-op (the cycle stays ``done``).
"""
from __future__ import annotations

import logging
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal

from django.utils import timezone

from . import autopilot_risk, cost_model, sleeves
from .models import AutopilotRun, PortfolioTarget, RebalanceOrder
from .rebalance import CurrentPosition, RebalanceConfig, compute_orders

log = logging.getLogger(__name__)

# RebalanceOrder 4-way side → BrokerOrder 2-way side.
_BROKER_SIDE = {"buy": "buy", "cover": "buy", "sell": "sell", "short": "sell"}
_FALLBACK_PRICE = 100.0


def _venue_quantity(side: str, qty: Decimal, *, is_demo: bool) -> Decimal | None:
    """Adjust an order quantity for venue constraints before emission.

    Alpaca rejects fractional shorts ("fractional orders cannot be sold short")
    and cannot hold a fractional short position — so on the credentialed venue a
    short/cover (anything touching the short side) is rounded DOWN to whole
    shares; ``None`` means the residual is < 1 share and the order is dropped.
    Long buys/sells and the demo book keep fractional sizing.
    """
    if is_demo or side not in ("short", "cover"):
        return qty
    whole = qty.to_integral_value(rounding=ROUND_DOWN)
    return whole if whole > 0 else None


def _active_link(strategy):
    """The strategy's active paper broker link, or None. Lazy import keeps
    apps.portfolios import-clean of apps.brokers at module load."""
    from apps.brokers.models import StrategyBrokerLink

    return (
        StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True)
        .select_related("broker_account", "broker_account__portfolio")
        .first()
    )


def _finalize_target(target: PortfolioTarget) -> dict | None:
    """Terminal hook: bridge a finished council cycle to the broker, if linked.

    No-op (returns None) for any strategy without an execution context (no fund
    sleeve on a configured fund, no legacy link) or whose autopilot is absent /
    disabled / halted — so the default, non-autopilot cycle path is
    byte-identical to pre-P7.
    """
    from .models import StrategyAutopilot

    strategy = target.strategy
    ctx = sleeves.execution_context(strategy)
    if ctx is None:
        if sleeves.sleeve_for(strategy, include_inactive=True) is not None:
            log.info("fund not configured / member leaving; no emission strategy=%s", strategy.pk)
        return None
    autopilot = getattr(strategy, "autopilot", None)
    if autopilot is None or not autopilot.is_enabled:
        return None
    if autopilot.state == StrategyAutopilot.STATE_HALTED:
        log.info("autopilot halted; skipping emission strategy=%s", strategy.pk)
        return None
    try:
        return maybe_emit_and_submit(target, link=ctx.link, autopilot=autopilot, ctx=ctx)
    except Exception:  # noqa: BLE001 — a bridge failure must not flip the cycle to failed
        log.exception("autopilot bridge failed target=%s", target.pk)
        return None


def _resolve_run(autopilot) -> AutopilotRun | None:
    """Best-effort: the open AutopilotRun for this autopilot (one cycle in flight
    at a time on the weekly cadence). Used only to attach audit; idempotency does
    not depend on it (the dispatcher's unique (autopilot, fire_time) does)."""
    return (
        AutopilotRun.objects.filter(
            autopilot=autopilot,
            status__in=(AutopilotRun.PENDING, AutopilotRun.RUNNING),
        )
        .order_by("-fire_time_utc")
        .first()
    )


def _seed_last_close(target: PortfolioTarget, book) -> dict[str, float]:
    """Prices for the book rebalance: the cycle's own limit prices (computed by
    finalize_cycle from last closes) for target names, plus each held position's
    avg_cost as a fallback for names being closed. ``book`` is the Portfolio
    being rebalanced (the sleeve, or the whole account on the legacy path)."""
    last_close: dict[str, float] = {}
    for ro in target.orders.all():
        if ro.limit_price:
            last_close[ro.ticker] = float(ro.limit_price)
    if book is not None:
        for pos in book.positions.all():
            last_close.setdefault(pos.ticker, float(pos.avg_cost or _FALLBACK_PRICE))
    return last_close


def _market_closed_for(account) -> bool:
    """Whether to hold orders locally. The demo broker is a simulator with no
    session, so it is always submittable; a credentialed (Alpaca) account holds
    pending_open when the NYSE session is closed (§6.6)."""
    from apps.brokers.capabilities import AUTH_NONE, get_capabilities
    from apps.brokers.market_calendar import is_market_open

    cap = get_capabilities(account.broker)
    if cap is not None and cap.auth_kind == AUTH_NONE:
        return False
    return not is_market_open()


def maybe_emit_and_submit(target: PortfolioTarget, *, link=None, autopilot, ctx=None) -> dict:
    """Convert the finished cycle's target into paper broker orders on the
    strategy's account and submit them (autonomously, paper-only). Returns an
    audit dict. ``ctx`` (``sleeves.ExecutionContext``) names the account, the
    book of record (sleeve or whole account) and the sleeve tag; it is resolved
    from the strategy when omitted (``link`` alone is the legacy call shape).
    """
    from apps.brokers.capabilities import AUTH_NONE, get_capabilities
    from apps.brokers.market_calendar import next_open

    strategy = target.strategy
    if ctx is None:
        ctx = sleeves.execution_context(strategy)
    if ctx is None and link is not None:
        ctx = sleeves.ExecutionContext(
            account=link.broker_account, book=link.broker_account.portfolio,
            sleeve=None, link=link,
        )
    if ctx is None:
        return {"enabled": False, "skipped_all": "no execution context"}
    account = ctx.account
    book = ctx.book
    sleeve = ctx.sleeve
    user = strategy.user
    run = _resolve_run(autopilot)
    if run is not None and run.status not in (AutopilotRun.PENDING, AutopilotRun.RUNNING):
        # Already finalized for this fire — don't re-emit.
        return run.submit_decision or {"enabled": True, "skipped_all": "already_submitted"}

    guardrail: dict = {}
    as_of = target.as_of_date

    from .models import PortfolioStrategy, StrategyAutopilot

    # Deterministic kinds (risk_parity / pairs) arrive already sized to
    # target_gross_pct and capped at their per-sleeve max/min inside their
    # constructor, and the backtest applies no vol-target or equity-cap pass —
    # so the bridge must NOT re-apply 1a/1c, or live would diverge from the
    # validated backtest (ADR 0025 §2). 1b (the live-only drawdown soft-cut) and
    # steps 2–3 (broker-book delta, risk gate, caps, paper-only) bind for ALL
    # kinds. The submit-time risk_check (step 3) still enforces the strategy's
    # max_position_pct — for risk_parity that is configured to the per-sleeve cap.
    weights = {t: float(w) for t, w in (target.target_weights or {}).items()}
    deterministic = strategy.kind in PortfolioStrategy.DETERMINISTIC_KINDS

    # Defense-in-depth: a deterministic book is emitted verbatim, but the
    # submit-gate (step 3) still enforces max_position_pct. If that is below the
    # per-sleeve cap the constructor sizes to, every sleeve is gate-rejected and
    # the book silently trades nothing. Part C sets max_position_pct ==
    # per_etf_max_pct to avoid this; surface a misconfig in the run audit so it
    # can never be a silent no-trade (ADR 0025 §3).
    if deterministic and strategy.max_position_pct < strategy.per_etf_max_pct:
        guardrail["cap_misconfig"] = {
            "max_position_pct": float(strategy.max_position_pct),
            "per_etf_max_pct": float(strategy.per_etf_max_pct),
        }
        log.warning(
            "deterministic strategy=%s max_position_pct=%.4f < per_etf_max_pct=%.4f "
            "— the submit-gate will reject over-cap sleeves (silent no-trade)",
            strategy.pk, float(strategy.max_position_pct), float(strategy.per_etf_max_pct),
        )

    # 1a. Volatility targeting — de-gross to the target vol (capped at 1.0).
    if not deterministic:
        scale, vol_audit = autopilot_risk.vol_target_scale(weights, autopilot, as_of, user)
        guardrail["vol_target"] = vol_audit
        if scale < 1.0:
            weights = {t: w * scale for t, w in weights.items()}

    # 1b. Soft-cut: a −5% drawdown halves the next cycle's gross (§6.3). A
    # live-only circuit-breaker with no backtest analog — binds for every kind.
    if autopilot.state == StrategyAutopilot.STATE_SOFT_CUT:
        weights = {t: w * 0.5 for t, w in weights.items()}
        guardrail["soft_cut_gross_scale"] = 0.5

    # 1c. Per-name / sector caps (the binding constructor math, re-applied).
    if not deterministic:
        weights, cap_notes = autopilot_risk.apply_caps(weights, strategy, sector_of=None)
        if cap_notes:
            guardrail["sector_caps"] = cap_notes

    # 2. Recompute the rebalance against the strategy's REAL book of record —
    #    its sleeve inside the shared fund account (P14), or the whole account
    #    on the legacy one-strategy-per-account path. Sizing NAV is the BOOK's
    #    marked value, so a sleeve trades its own capital only.
    pf = book
    current = [
        CurrentPosition(
            ticker=p.ticker, quantity=float(p.quantity),
            avg_cost=float(p.avg_cost or 0.0), sector=p.sector,
        )
        for p in pf.positions.all()
    ]
    last_close = _seed_last_close(target, pf)
    from .valuation import value_portfolio

    try:
        nav = float(value_portfolio(pf).total_value)
    except Exception:  # noqa: BLE001 — fall back to the allocation if marks fail
        nav = float(ctx.fallback_nav)
    if nav <= 0:
        # An unfunded sleeve (fund not reset yet) must not size off a phantom
        # $100k — trade nothing and say why.
        if sleeve is not None:
            guardrail["unfunded_sleeve"] = True
            log.warning("sleeve unfunded; no orders strategy=%s sleeve=%s", strategy.pk, sleeve.pk)
            nav = 0.0
        else:
            nav = float(ctx.fallback_nav) or 100000.0
    if sleeve is not None:
        guardrail["sleeve"] = {"id": sleeve.pk, "nav": round(nav, 2)}
    cfg = RebalanceConfig(
        portfolio_value=max(1.0, nav),
        last_close=last_close,
        min_trade_notional_usd=float(strategy.min_trade_notional_usd),
        max_turnover_pct=float(strategy.max_turnover_pct),
    )
    orders = compute_orders(current, weights, cfg) if nav > 0 else []

    # Replace the cycle's strategy-book RebalanceOrders (computed against the
    # un-enrolled strategy portfolio) with these book deltas — for a fund member
    # / linked strategy the sleeve / broker book is the book of record.
    target.orders.all().delete()
    rebalance_rows = RebalanceOrder.objects.bulk_create([
        RebalanceOrder(
            target=target, ticker=o.ticker, side=o.side,
            quantity=Decimal(str(round(o.quantity, 6))),
            limit_price=Decimal(str(round(o.limit_price, 4))) if o.limit_price else None,
            reason=o.reason,
            estimated_notional_usd=Decimal(str(round(o.estimated_notional_usd, 2))),
            sequence=o.sequence,
        )
        for o in orders
    ])

    # 3. Emit + submit each order through the gated, idempotent, capped path.
    cap = get_capabilities(account.broker)
    is_demo = cap is not None and cap.auth_kind == AUTH_NONE
    market_closed = _market_closed_for(account)
    risk_check = autopilot_risk.make_risk_check(strategy, book=pf)

    max_orders = int(autopilot.max_orders_per_day or 0)
    max_notional = Decimal(str(autopilot.max_notional_per_day_usd or 0))
    # Daily caps are per strategy: on the shared fund account count only THIS
    # sleeve's orders, else a busy sibling would starve it.
    n_today, notional_today = _prior_24h(account, sleeve=sleeve)

    try:
        provider = autopilot_risk._data_provider(user)
    except Exception:  # noqa: BLE001 — ADV is best-effort; no floor if unavailable
        provider = None

    placed: list = []
    items: list[dict] = []
    min_notional = Decimal(str(strategy.min_trade_notional_usd or 0))
    for ro in rebalance_rows:
        # §6.5 liquidity floor: cap the order so its notional ≤ liquidity_adv_cap_pct
        # of the name's 20-day dollar-ADV. Missing ADV ⇒ no floor (logged), never block.
        price = ro.limit_price or Decimal(str(last_close.get(ro.ticker, _FALLBACK_PRICE)))
        adv = cost_model.dollar_adv(ro.ticker, as_of, provider) if provider else None
        cap_shares = cost_model.liquidity_cap_shares(adv, autopilot.liquidity_adv_cap_pct, price)
        if cap_shares is not None and Decimal("0") <= cap_shares < ro.quantity:
            ro.quantity = cap_shares.quantize(Decimal("0.000001"))
            ro.estimated_notional_usd = (ro.quantity * price).quantize(Decimal("0.01"))
            ro.save(update_fields=["quantity", "estimated_notional_usd"])
            guardrail.setdefault("liquidity_capped", []).append(ro.ticker)
        # §6.4 cost discipline: an order shrunk below the no-trade band by the
        # liquidity cap isn't worth its spread — skip it. Impact is reported.
        impact = cost_model.market_impact_bps(ro.estimated_notional_usd or Decimal("0"), adv)
        if impact is not None:
            guardrail.setdefault("impact_bps", {})[ro.ticker] = round(impact, 1)
        if (ro.estimated_notional_usd or Decimal("0")) < min_notional:
            items.append({"ticker": ro.ticker, "skipped": "below min notional after liquidity cap"})
            continue
        order_notional = (ro.estimated_notional_usd or Decimal("0"))
        if max_orders and n_today >= max_orders:
            items.append({"ticker": ro.ticker, "skipped": "daily order cap"})
            continue
        if max_notional and (notional_today + order_notional) > max_notional:
            items.append({"ticker": ro.ticker, "skipped": "daily notional cap"})
            continue
        venue_qty = _venue_quantity(ro.side, ro.quantity, is_demo=is_demo)
        if venue_qty is None:
            items.append({"ticker": ro.ticker, "skipped": "fractional short < 1 share"})
            continue
        try:
            order = _emit_one(
                account=account, user=user,
                client_order_id=f"rbo-{ro.id}", ticker=ro.ticker,
                broker_side=_BROKER_SIDE.get(ro.side, "buy"), quantity=venue_qty,
                rebalance_order=ro, is_demo=is_demo,
                market_closed=market_closed, risk_check=risk_check,
                next_open_fn=next_open, sleeve=sleeve,
            )
        except _RiskRejected as exc:
            items.append({"ticker": ro.ticker, "rejected": str(exc)[:200]})
            continue
        except Exception as exc:  # noqa: BLE001 — one bad order can't abort the batch
            log.exception("autopilot emit failed ticker=%s", ro.ticker)
            items.append({"ticker": ro.ticker, "error": str(exc)[:200]})
            continue
        placed.append(order)
        if order.status != order.STATUS_PENDING_OPEN:
            n_today += 1
            notional_today += order_notional
        items.append({
            "ticker": ro.ticker, "side": order.side, "quantity": str(order.quantity),
            "order_id": order.id, "status": order.status,
            "client_order_id": order.client_order_id,
        })

    # 4. Terminal status (kept out of ACTIVE_STATUSES).
    PortfolioTarget.objects.filter(pk=target.pk).update(
        status=PortfolioTarget.AUTOPILOT_SUBMITTED
    )

    decision = {
        "enabled": True,
        "account": account.id,
        "sleeve": sleeve.id if sleeve is not None else None,
        "mode": "demo" if is_demo else "paper",
        "market_closed": market_closed,
        "orders": len(rebalance_rows),
        "submitted": len([o for o in placed if o.status != o.STATUS_PENDING_OPEN]),
        "pending_open": len([o for o in placed if o.status == o.STATUS_PENDING_OPEN]),
        "items": items,
    }
    if run is not None:
        run.target = target
        run.status = AutopilotRun.SUBMITTED
        run.submit_decision = decision
        run.guardrail_actions = {**run.guardrail_actions, **guardrail}
        run.finished_at = timezone.now()
        run.save(update_fields=[
            "target", "status", "submit_decision", "guardrail_actions", "finished_at",
        ])
        if placed:
            run.broker_orders.add(*placed)

    # Notify (cycle done; cap breaches are visible in the item list).
    try:
        from apps.notifications.autopilot import CYCLE_DONE, notify_autopilot

        n_sub = decision["submitted"]
        n_pending = decision["pending_open"]
        n_skip = len([i for i in items if "skipped" in i or "rejected" in i])
        notify_autopilot(
            autopilot, CYCLE_DONE,
            f"Cycle {as_of}: {n_sub} submitted, {n_pending} held for open, "
            f"{n_skip} skipped/rejected on {account.label}.",
        )
    except Exception:  # noqa: BLE001
        pass
    return decision


def flatten_to_cash(autopilot) -> dict:
    """§6.3 flatten-on-halt: liquidate the broker book to cash through the SAME
    gated, idempotent path as any order (no side channel). Idempotent per
    (autopilot, ticker) via a deterministic ``flat-<ap>-<ticker>`` order id."""
    from apps.brokers.capabilities import AUTH_NONE, get_capabilities
    from apps.brokers.market_calendar import next_open
    from apps.brokers.models import BrokerOrder

    strategy = autopilot.strategy
    # The book to liquidate is the strategy's OWN: its sleeve on the shared fund
    # account (never the siblings' holdings), else the legacy whole account.
    ctx = sleeves.execution_context(strategy)
    if ctx is not None:
        account, book, sleeve = ctx.account, ctx.book, ctx.sleeve
    else:
        account = autopilot.broker_account
        book = getattr(account, "portfolio", None) if account is not None else None
        sleeve = None
    if account is None or book is None:
        return {"flattened": 0}
    user = strategy.user
    positions = list(book.positions.all())
    if not positions:
        return {"flattened": 0}
    current = [
        CurrentPosition(
            ticker=p.ticker, quantity=float(p.quantity),
            avg_cost=float(p.avg_cost or 0.0), sector=p.sector,
        )
        for p in positions
    ]
    last_close = {p.ticker: float(p.avg_cost or _FALLBACK_PRICE) for p in positions}
    cfg = RebalanceConfig(
        portfolio_value=1.0, last_close=last_close,
        min_trade_notional_usd=0.0, max_turnover_pct=1.0,
    )
    orders = compute_orders(current, {}, cfg)  # empty target ⇒ all closes
    cap = get_capabilities(account.broker)
    is_demo = cap is not None and cap.auth_kind == AUTH_NONE
    market_closed = _market_closed_for(account)
    risk_check = autopilot_risk.make_risk_check(strategy, book=book)
    placed = []
    for o in orders:
        cid = f"flat-{autopilot.id}-{o.ticker.upper()}"
        if BrokerOrder.objects.filter(client_order_id=cid).exists():
            continue  # already flattened this name
        try:
            placed.append(_emit_one(
                account=account, user=user, client_order_id=cid, ticker=o.ticker,
                broker_side=_BROKER_SIDE.get(o.side, "sell"),
                quantity=Decimal(str(round(o.quantity, 6))),
                is_demo=is_demo, market_closed=market_closed,
                risk_check=risk_check, next_open_fn=next_open, sleeve=sleeve,
            ))
        except Exception:  # noqa: BLE001 — one bad close can't abort the liquidation
            log.exception("flatten emit failed ticker=%s", o.ticker)
    return {"flattened": len(placed)}


def _strategy_for_account(account):
    """The strategy whose active link points at this account (or the order's
    cycle strategy). Used to resolve the risk_check on release — legacy path;
    a sleeve-tagged order resolves through its sleeve instead."""
    from apps.brokers.models import StrategyBrokerLink

    link = (
        StrategyBrokerLink.objects.filter(broker_account=account, is_active=True)
        .select_related("strategy")
        .first()
    )
    return link.strategy if link else None


def _risk_check_for_order(order):
    """The submit-time cap check for a held order: against its sleeve's book when
    tagged (P14), else the account's linked strategy on the whole account."""
    account = order.broker_account
    sleeve = getattr(order, "sleeve", None)
    if sleeve is not None:
        return autopilot_risk.make_risk_check(sleeve.strategy, book=sleeve.portfolio)
    strategy = _strategy_for_account(account)
    if strategy is None:
        return lambda o: []
    return autopilot_risk.make_risk_check(strategy, book=account.portfolio)


def venue_fit(order) -> dict | None:
    """Fit a sleeve's order to what the venue lets ONE account do (P14 follow-up).

    On the shared fund account two sleeves can legitimately sit on opposite sides
    of the same name, so a sleeve's order can be — at the ACCOUNT level — a
    position flip or a fractional short, both of which Alpaca refuses in one shot:

      * no zero-crossing in a single order ("insufficient qty available for order
        (requested: N, available: M)" — flatten first);
      * short positions are whole shares only ("fractional orders cannot be sold
        short").

    Fit the order instead of letting the venue reject it: a crossing order is
    clamped to the quantity that takes the account FLAT (the sleeve books the
    actual fill; the remainder is re-targeted next cycle), and any quantity that
    leaves the account short is rounded DOWN to whole shares (a residual < 1
    share is rejected locally with a clear message). Same-side adds, reductions
    inside a long, and whole-share shorts opened/covered from flat pass through
    unchanged. Credentialed venues only.

    Returns the audit record when the order was changed, else ``None``.
    """
    from apps.brokers.models import BrokerOrder

    pf = getattr(order.broker_account, "portfolio", None)
    if pf is None:
        return None
    pos = pf.positions.filter(ticker=order.ticker.upper()).first()
    held = Decimal(str(pos.quantity)) if pos is not None else Decimal("0")
    requested = Decimal(str(order.quantity))
    qty = requested
    sign = Decimal("1") if order.side == "buy" else Decimal("-1")
    after = held + sign * qty
    notes: list[str] = []
    if held != 0 and after != 0 and ((held > 0) != (after > 0)):
        qty = abs(held)  # close to flat, never through zero
        after = Decimal("0")
        notes.append(f"clamped to account-flat (held {held}, requested {requested})")
    if held < 0 or after < 0:  # the account is / ends up short here
        whole = qty.to_integral_value(rounding=ROUND_FLOOR)
        if whole != qty:
            notes.append(f"rounded {qty} -> {whole} (venue: whole-share shorts only)")
            qty = whole
    if not notes:
        return None
    record = {
        "order_id": order.pk, "ticker": order.ticker, "side": order.side,
        "requested": str(requested), "held": str(held), "quantity": str(qty),
        "notes": notes,
    }
    if qty <= 0:
        message = (
            "venue fit: nothing left to trade on the shared account — "
            + "; ".join(notes)
        )[:500]
        BrokerOrder.objects.filter(pk=order.pk).update(
            status=BrokerOrder.STATUS_REJECTED, error_message=message,
        )
        order.status = BrokerOrder.STATUS_REJECTED
        order.error_message = message
        record["rejected"] = True
    else:
        BrokerOrder.objects.filter(pk=order.pk).update(quantity=qty)
        order.quantity = qty
    _record_venue_fit(order, record)
    return record


def _record_venue_fit(order, record: dict) -> None:
    """Audit trail for :func:`venue_fit`: the linked AutopilotRun's
    ``guardrail_actions["account_venue_fit"]`` (a list — one run can fit several
    orders) plus a warning log. Best-effort: never raises into the submit path."""
    log.warning("venue fit order=%s %s", order.pk, record)
    try:
        run = order.autopilot_runs.order_by("-fire_time_utc").first()
    except Exception:  # noqa: BLE001
        run = None
    if run is None:
        return
    actions = dict(run.guardrail_actions or {})
    actions["account_venue_fit"] = [*actions.get("account_venue_fit", []), record]
    AutopilotRun.objects.filter(pk=run.pk).update(guardrail_actions=actions)


def submit_held_order(order) -> bool:
    """Release a ``pending_open`` order at the open: reset to draft, venue-fit it
    to the account's net position, then gate (risk_check wired) + submit
    idempotently — or demo-fill. The daily-cap check is the caller's (evaluated
    at release time, §6.6). Returns True if submitted."""
    from apps.brokers.capabilities import AUTH_NONE, get_capabilities
    from apps.brokers.confirmation import ConfirmationError, GateContext, gate
    from apps.brokers.idempotency import submit_idempotent
    from apps.brokers.models import BrokerOrder
    from apps.brokers.reconcile import get_broker

    account = order.broker_account
    cap = get_capabilities(account.broker)
    is_demo = cap is not None and cap.auth_kind == AUTH_NONE
    user = account.user
    risk_check = _risk_check_for_order(order)

    # gate() only confirms a draft, so clear the local hold first.
    BrokerOrder.objects.filter(pk=order.pk).update(
        status=BrokerOrder.STATUS_DRAFT, release_after=None,
    )
    order.refresh_from_db()

    if is_demo:
        reasons = list(risk_check(order) or [])
        if reasons:
            BrokerOrder.objects.filter(pk=order.pk).update(
                status=BrokerOrder.STATUS_REJECTED, error_message="; ".join(reasons)[:500],
            )
            return False
        from apps.brokers.demo_fills import place_demo_order

        place_demo_order(order, user=user)
        BrokerOrder.objects.filter(pk=order.pk).update(
            confirmation_method=BrokerOrder.CONFIRM_SCHEDULED,
        )
        return True

    # Two sleeves share this account, so fit the order to its NET position first.
    fit = venue_fit(order)
    if fit is not None and fit.get("rejected"):
        return False

    try:
        gate(order, GateContext(
            user=user, confirmation_method=BrokerOrder.CONFIRM_SCHEDULED,
            bypass_typed=True, risk_check=risk_check,
        ))
    except ConfirmationError as exc:
        BrokerOrder.objects.filter(pk=order.pk).update(
            status=BrokerOrder.STATUS_REJECTED, error_message=str(exc)[:500],
        )
        return False
    submit_idempotent(order=order, broker=get_broker(account))
    return True


class _RiskRejected(Exception):
    pass


def _emit_one(
    *, account, user, client_order_id, ticker, broker_side, quantity,
    rebalance_order=None, is_demo, market_closed, risk_check, next_open_fn,
    sleeve=None,
):
    """Create one BrokerOrder and route it: held ``pending_open`` if the market
    is closed, demo-filled, or gated (risk_check wired) + submitted idempotently.
    Shared by the cycle bridge, the flatten paths and the fund reset — no side
    channel. ``sleeve`` tags the order so its fills attribute to that sleeve."""
    from apps.brokers.models import BrokerOrder

    order = BrokerOrder.objects.create(
        broker_account=account,
        client_order_id=client_order_id,  # deterministic — overrides the UUID4 default
        rebalance_order=rebalance_order,
        sleeve=sleeve,
        ticker=ticker.upper(),
        side=broker_side,
        quantity=quantity,
        order_type="market",
    )

    if market_closed:
        # Hold locally; the release beat task submits at the next open (§6.6).
        order.status = BrokerOrder.STATUS_PENDING_OPEN
        order.release_after = next_open_fn()
        order.save(update_fields=["status", "release_after"])
        return order

    if is_demo:
        # Demo broker skips the gate → enforce the risk check here so caps bind
        # on the demo path used by the deterministic tests.
        reasons = list(risk_check(order) or [])
        if reasons:
            BrokerOrder.objects.filter(pk=order.pk).update(
                status=BrokerOrder.STATUS_REJECTED,
                error_message="; ".join(reasons)[:500],
            )
            raise _RiskRejected("; ".join(reasons))
        from apps.brokers.demo_fills import place_demo_order

        place_demo_order(order, user=user)
        BrokerOrder.objects.filter(pk=order.pk).update(
            confirmation_method=BrokerOrder.CONFIRM_SCHEDULED
        )
        order.refresh_from_db()
        return order

    # Credentialed paper: gate (live hard-blocked; risk_check wired) + submit.
    from apps.brokers.confirmation import ConfirmationError, GateContext, gate
    from apps.brokers.idempotency import submit_idempotent
    from apps.brokers.reconcile import get_broker

    # Two sleeves can share this account, so fit the order to its NET position.
    fit = venue_fit(order)
    if fit is not None and fit.get("rejected"):
        raise _RiskRejected(order.error_message)

    try:
        gate(order, GateContext(
            user=user,
            confirmation_method=BrokerOrder.CONFIRM_SCHEDULED,
            bypass_typed=True,
            risk_check=risk_check,
        ))
    except ConfirmationError as exc:
        BrokerOrder.objects.filter(pk=order.pk).update(
            status=BrokerOrder.STATUS_REJECTED,
            error_message=str(exc)[:500],
        )
        raise _RiskRejected(str(exc)) from exc
    submit_idempotent(order=order, broker=get_broker(account))
    order.refresh_from_db()
    return order


def _prior_24h(account, *, sleeve=None) -> tuple[int, Decimal]:
    """(count, notional) of autopilot orders submitted on this account in the
    trailing 24h — the daily-cap basis across staggered fires. With ``sleeve``
    (a fund member on the shared account) only that sleeve's orders count."""
    import datetime as dt

    from apps.brokers.models import BrokerOrder

    cutoff = timezone.now() - dt.timedelta(hours=24)
    prior = (
        BrokerOrder.objects.filter(
            broker_account=account,
            autopilot_runs__isnull=False,
            created_at__gte=cutoff,
        )
        .exclude(status=BrokerOrder.STATUS_PENDING_OPEN)
        .distinct()
    )
    if sleeve is not None:
        prior = prior.filter(sleeve=sleeve)
    fallback = Decimal(str(_FALLBACK_PRICE))
    notional = sum(
        (
            Decimal(str(o.quantity)) * (o.avg_fill_price or o.limit_price or fallback)
            for o in prior
        ),
        Decimal("0"),
    )
    return prior.count(), notional
