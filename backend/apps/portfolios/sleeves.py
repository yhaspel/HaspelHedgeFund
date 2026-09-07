"""P14 — fund sleeves: the shared-account attribution layer.

The fund trades ONE paper broker account. Each member strategy owns a *sleeve*
(``FundSleeve`` + a ``kind="sleeve"`` Portfolio): the slice of that account the
strategy is responsible for. The bridge sizes a strategy's target against its
sleeve's NAV and trades the delta against the sleeve's own positions, so two
strategies never touch each other's holdings; fills carry ``BrokerOrder.sleeve``
and are applied to the sleeve ledger as well as the account book (reconcile).

Truth vs attribution: the account book (reconciled against the broker) is the
truth for fund NAV / the fund drawdown breaker; sleeves are attribution. The
difference (Σ sleeves vs account) is the "unallocated" residual — legacy
positions, manual tickets, or drift — reported, never silently traded.

Slices FLOAT with their own P&L (decision 2026-09-03): ``allocation_pct`` is
applied to the account's cash only by ``reset_fund`` (fresh start) and when a
member joins (from unallocated cash). No cross-sleeve re-leveling.

This module is the single writer for fund configuration (account, members,
reset, flatten); ``api_fund`` is a thin HTTP layer over it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import (
    AutonomousFund,
    FundSleeve,
    LedgerEntry,
    Portfolio,
    PortfolioStrategy,
    Position,
    StrategyAutopilot,
)

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
# Active sleeves must sum to 100% (whole percent); a hair of rounding slack.
ALLOCATION_TOLERANCE_PCT = Decimal("0.05")
# Default cadences for new members: Friday close ET, staggered 15 min apart so
# the pods never fire in the same minute (ADR 0018 §3.A carried over).
_FIRST_SLOT_MINUTES = 16 * 60 + 30
_SLOT_STEP_MINUTES = 15


class FundError(Exception):
    """A fund configuration change that cannot be applied as asked. ``detail``
    is user-facing; ``extra`` carries structured context for the API."""

    def __init__(self, detail: str, *, status: int = 409, **extra):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.extra = extra


# ---------------------------------------------------------------------------
# Resolution — which book / account does a strategy trade?
# ---------------------------------------------------------------------------
@dataclass
class ExecutionContext:
    """Everything the bridge needs to turn a strategy's target into orders."""

    account: object            # brokers.BrokerAccount — where orders go
    book: Portfolio            # the book to size against / rebalance (sleeve or account)
    sleeve: FundSleeve | None  # None on the legacy one-strategy-per-account path
    link: object | None        # brokers.StrategyBrokerLink (legacy fallback NAV)

    @property
    def fallback_nav(self) -> Decimal:
        if self.sleeve is not None and self.sleeve.initial_capital_usd:
            return Decimal(self.sleeve.initial_capital_usd)
        if self.link is not None and getattr(self.link, "allocation_usd", None):
            return Decimal(self.link.allocation_usd)
        return Decimal("100000")


def sleeve_for(strategy, *, include_inactive: bool = False) -> FundSleeve | None:
    """The strategy's fund sleeve (active only by default), or None."""
    qs = FundSleeve.objects.filter(strategy_id=strategy.pk).select_related(
        "fund", "fund__broker_account", "fund__broker_account__portfolio", "portfolio",
    )
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return qs.first()


def fund_halted(strategy) -> bool:
    """True when this strategy is a member of a fund whose kill switch is ON.

    The fund halt is firm-wide and LATCHED: nothing a member does (a member-level
    Resume, an hourly guardrail sweep that finds no drawdown, a Run-now) may
    trade through it — ``POST /api/fund/resume/`` is the only exit. Flatten /
    reset still work, because they go through ``_emit_close`` rather than the
    cycle bridge.
    """
    sleeve = sleeve_for(strategy, include_inactive=True)
    if sleeve is None:
        return False
    return sleeve.fund.state == AutonomousFund.STATE_HALTED


def _active_link(strategy):
    from apps.brokers.models import StrategyBrokerLink

    return (
        StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True)
        .select_related("broker_account", "broker_account__portfolio")
        .first()
    )


def execution_context(strategy) -> ExecutionContext | None:
    """Sleeve-first: a member of a configured fund trades its sleeve inside the
    fund's shared account. A member of an UNconfigured fund (no account yet)
    cannot trade at all (None). A strategy with no sleeve falls back to the
    legacy per-strategy broker link (whole-account book)."""
    sleeve = sleeve_for(strategy, include_inactive=True)
    if sleeve is not None:
        if not sleeve.is_active:
            return None  # leaving the fund — no new cycles, closes only
        fund = sleeve.fund
        if fund.broker_account_id is None:
            return None
        return ExecutionContext(
            account=fund.broker_account, book=sleeve.portfolio, sleeve=sleeve,
            link=_active_link(strategy),
        )
    link = _active_link(strategy)
    if link is None:
        return None
    return ExecutionContext(
        account=link.broker_account, book=link.broker_account.portfolio,
        sleeve=None, link=link,
    )


def member_book(strategy) -> Portfolio | None:
    """The book whose equity IS this strategy's performance: its fund sleeve
    (even while the fund is unconfigured — an empty sleeve is the honest
    picture until Reset funds it), else its legacy linked account book, else
    None."""
    sleeve = sleeve_for(strategy)
    if sleeve is not None:
        return sleeve.portfolio
    ctx = execution_context(strategy)
    return ctx.book if ctx is not None else None


def book_value(book: Portfolio | None) -> Decimal | None:
    """Marked NAV of a book, cost-basis fallback when marks fail, None if no book."""
    if book is None:
        return None
    from .valuation import value_portfolio

    try:
        return Decimal(str(value_portfolio(book).total_value))
    except Exception:  # noqa: BLE001 — marks are best-effort; cost basis stands in
        nav = Decimal(str(book.cash_balance or 0))
        for pos in book.positions.all():
            nav += abs(Decimal(str(pos.quantity))) * Decimal(str(pos.avg_cost or 0))
        return nav


def account_book(fund: AutonomousFund) -> Portfolio | None:
    """The shared account's book — always re-read from the database: fills and
    reconciles move its cash between two calls in one request (readiness →
    reset), and a cached relation would split stale cash."""
    if fund.broker_account_id is None:
        return None
    from apps.brokers.models import BrokerAccount

    pf_id = (
        BrokerAccount.objects.filter(pk=fund.broker_account_id)
        .values_list("portfolio_id", flat=True).first()
    )
    return Portfolio.objects.filter(pk=pf_id).first() if pf_id else None


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
def default_cron_for(fund: AutonomousFund) -> str:
    """Next free 15-minute Friday-close slot after the existing members'."""
    taken = set(
        StrategyAutopilot.objects.filter(
            strategy__fund_sleeve__fund=fund, strategy__fund_sleeve__is_active=True,
        ).values_list("cron_expression", flat=True)
    )
    for k in range(0, 8):
        minutes = _FIRST_SLOT_MINUTES + k * _SLOT_STEP_MINUTES
        cron = f"{minutes % 60} {minutes // 60} * * 5"
        if cron not in taken:
            return cron
    return "30 16 * * 5"


def _short_mode_for(strategy) -> str:
    if strategy.kind == PortfolioStrategy.KIND_LONG_SHORT:
        return StrategyAutopilot.SHORT_SINGLE_NAME
    return StrategyAutopilot.SHORT_CASH


def create_sleeve(fund: AutonomousFund, strategy, allocation_pct) -> FundSleeve:
    """Create (or re-activate) the strategy's sleeve in ``fund`` with an empty
    ledger; binds the broker link + a disabled autopilot when the fund has an
    account. Does NOT fund it — see ``fund_new_members`` / ``reset_fund``."""
    pct = Decimal(str(allocation_pct)).quantize(CENT)
    sleeve = FundSleeve.objects.filter(strategy=strategy).select_related("fund").first()
    if sleeve is not None and sleeve.fund_id != fund.id:
        raise FundError(
            f"{strategy.name} already belongs to another fund.", status=409,
            strategy_id=strategy.pk,
        )
    if sleeve is None:
        book = Portfolio.objects.create(
            user=fund.owner, kind=Portfolio.KIND_SLEEVE,
            name=f"Sleeve · {strategy.name}"[:64], cash_balance=Decimal("0"),
        )
        sleeve = FundSleeve.objects.create(
            fund=fund, strategy=strategy, portfolio=book, allocation_pct=pct,
        )
    else:
        sleeve.allocation_pct = pct
        sleeve.is_active = True
        sleeve.removed_at = None
        sleeve.save(update_fields=["allocation_pct", "is_active", "removed_at", "updated_at"])
    bind_member(fund, sleeve)
    return sleeve


def bind_member(fund: AutonomousFund, sleeve: FundSleeve) -> None:
    """Keep the strategy's execution binding in step with the fund: an active
    ``StrategyBrokerLink`` to the fund's account (paper-only guard lives on the
    link) and an autopilot row pointing at the same account (never auto-
    enabled). No-op while the fund has no account."""
    from apps.brokers.models import StrategyBrokerLink

    strategy = sleeve.strategy
    account = fund.broker_account
    if account is None:
        return
    link = StrategyBrokerLink.objects.filter(strategy=strategy, is_active=True).first()
    if link is not None and link.broker_account_id != account.id:
        link.is_active = False
        link.save(update_fields=["is_active", "updated_at"])
        link = None
    if link is None:
        link = StrategyBrokerLink.objects.create(
            strategy=strategy, broker_account=account, is_active=True,
            allocation_usd=sleeve.initial_capital_usd or Decimal("0"),
        )
    ap, _created = StrategyAutopilot.objects.get_or_create(
        strategy=strategy,
        defaults={
            "broker_account": account,
            "cron_expression": default_cron_for(fund),
            "model_preset": "frugal",
            "short_mode": _short_mode_for(strategy),
            "is_enabled": False,
        },
    )
    if ap.broker_account_id != account.id:
        ap.broker_account = account
        ap.save(update_fields=["broker_account", "updated_at"])


def deactivate_sleeve(sleeve: FundSleeve, *, reason: str) -> None:
    """Take a member out of the roster: stop its autopilot, retire its link,
    mark the sleeve inactive. Its ledger is kept (history + any closes still
    attributing fills). Cash left in an inactive sleeve counts as unallocated."""
    from apps.brokers.models import StrategyBrokerLink

    now = timezone.now()
    StrategyAutopilot.objects.filter(strategy_id=sleeve.strategy_id).update(
        is_enabled=False, next_run_at=None, updated_at=now,
    )
    StrategyBrokerLink.objects.filter(strategy_id=sleeve.strategy_id, is_active=True).update(
        is_active=False, updated_at=now,
    )
    sleeve.is_active = False
    sleeve.removed_at = now
    sleeve.save(update_fields=["is_active", "removed_at", "updated_at"])
    log.info(
        "sleeve deactivated sleeve=%s strategy=%s reason=%s",
        sleeve.pk, sleeve.strategy_id, reason,
    )


def validate_allocations(members: list[dict], owner) -> list[tuple[PortfolioStrategy, Decimal]]:
    """``[{strategy_id, allocation_pct}]`` → ``[(strategy, pct)]`` or FundError.
    Strategies must be the owner's, distinct; percentages ≥ 0 and summing to
    100 (±tolerance). An empty list is a valid "no members"."""
    if not isinstance(members, list):
        raise FundError("members must be a list", status=400)
    seen: set[int] = set()
    out: list[tuple[PortfolioStrategy, Decimal]] = []
    total = Decimal("0")
    for m in members:
        try:
            sid = int(m.get("strategy_id"))
            pct = Decimal(str(m.get("allocation_pct"))).quantize(CENT, rounding=ROUND_HALF_UP)
        except (TypeError, ValueError, ArithmeticError, AttributeError) as exc:
            raise FundError("each member needs strategy_id and allocation_pct", status=400) from exc
        if sid in seen:
            raise FundError(f"strategy {sid} listed twice", status=400)
        if pct < 0:
            raise FundError("allocation_pct cannot be negative", status=400)
        strategy = PortfolioStrategy.objects.filter(pk=sid, user=owner).first()
        if strategy is None:
            raise FundError(f"strategy {sid} not found", status=404)
        seen.add(sid)
        total += pct
        out.append((strategy, pct))
    if out and abs(total - Decimal("100")) > ALLOCATION_TOLERANCE_PCT:
        raise FundError(
            f"allocations must total 100% (got {total}%)", status=400, total_pct=str(total),
        )
    return out


def equal_split(n: int) -> list[Decimal]:
    """n equal whole-percent shares that sum exactly to 100 (last takes the residual)."""
    if n <= 0:
        return []
    base = (Decimal("100") / n).quantize(CENT)
    return [base] * (n - 1) + [Decimal("100") - base * (n - 1)]


def sleeve_positions(sleeve: FundSleeve) -> list[Position]:
    return list(sleeve.portfolio.positions.all().order_by("ticker"))


def set_members(fund: AutonomousFund, members: list[dict], *, force_flatten: bool = False) -> dict:
    """Replace the roster: add new sleeves, update allocations, remove absent
    members. Removal of a sleeve that still holds positions is refused (409 with
    the blocking names) unless ``force_flatten`` — then closing orders are queued
    for that sleeve and it leaves the roster as it flattens. New members are
    funded from the account's UNALLOCATED cash (never from other sleeves)."""
    wanted = validate_allocations(members, fund.owner)
    wanted_by_id = {s.pk: pct for s, pct in wanted}
    current = {
        sl.strategy_id: sl
        for sl in fund.sleeves.filter(is_active=True).select_related("strategy", "portfolio")
    }

    blocking = []
    for sid, sleeve in current.items():
        if sid in wanted_by_id:
            continue
        held = sleeve_positions(sleeve)
        if held and not force_flatten:
            blocking.append({
                "strategy_id": sid, "name": sleeve.strategy.name,
                "positions": [p.ticker for p in held],
            })
    if blocking:
        raise FundError(
            "some strategies still hold positions — flatten them first "
            "(or remove with flatten to queue their closing orders).",
            status=409, blocking=blocking,
        )

    summary: dict = {"added": [], "removed": [], "updated": [], "flattening": [], "warnings": []}
    with transaction.atomic():
        for sid, sleeve in current.items():
            if sid in wanted_by_id:
                continue
            held = sleeve_positions(sleeve)
            if held:
                res = flatten_sleeve(sleeve, reason="member removed")
                summary["flattening"].append({"strategy_id": sid, "orders": res.get("orders", 0)})
            else:
                # A flat sleeve hands its cash back to the pool (unallocated).
                _set_sleeve_cash(
                    sleeve, Decimal("0"), note="left the fund — cash returned to the pool",
                )
                sleeve.initial_capital_usd = Decimal("0")
                sleeve.save(update_fields=["initial_capital_usd", "updated_at"])
            deactivate_sleeve(sleeve, reason="removed from roster")
            summary["removed"].append(sid)

        new_sleeves: list[FundSleeve] = []
        for strategy, pct in wanted:
            sleeve = current.get(strategy.pk)
            if sleeve is not None:
                if sleeve.allocation_pct != pct:
                    sleeve.allocation_pct = pct
                    sleeve.save(update_fields=["allocation_pct", "updated_at"])
                    summary["updated"].append(strategy.pk)
                bind_member(fund, sleeve)
                continue
            sleeve = create_sleeve(fund, strategy, pct)
            new_sleeves.append(sleeve)
            summary["added"].append(strategy.pk)

        if new_sleeves and fund.broker_account_id is not None:
            summary["warnings"].extend(fund_new_members(fund, new_sleeves))
        elif new_sleeves:
            summary["warnings"].append(
                "Choose the fund's paper account, then Reset to fund the new sleeves."
            )
    return summary


def unallocated_cash(fund: AutonomousFund) -> Decimal | None:
    """Account cash not attributed to any ACTIVE sleeve (None if no account)."""
    book = account_book(fund)
    if book is None:
        return None
    attributed = sum(
        (Decimal(str(sl.portfolio.cash_balance or 0)) for sl in fund.active_sleeves()),
        Decimal("0"),
    )
    return (Decimal(str(book.cash_balance or 0)) - attributed).quantize(CENT)


def fund_new_members(fund: AutonomousFund, sleeves: list[FundSleeve]) -> list[str]:
    """Give freshly added sleeves their share of the UNALLOCATED cash, pro-rata to
    their allocation % — existing sleeves keep their capital (slices float).
    Returns user-facing warnings (e.g. nothing left to allocate)."""
    warnings: list[str] = []
    free = unallocated_cash(fund)
    if free is None:
        return warnings
    if free <= 0:
        warnings.append(
            "No unallocated cash in the account — the new sleeve(s) start at $0. "
            "Reset the fund to re-apply the allocation split."
        )
        return warnings
    total_pct = sum((Decimal(sl.allocation_pct) for sl in sleeves), Decimal("0"))
    if total_pct <= 0:
        return warnings
    remaining = free
    for i, sleeve in enumerate(sleeves):
        if i == len(sleeves) - 1:
            share = remaining
        else:
            share = (free * Decimal(sleeve.allocation_pct) / total_pct).quantize(CENT)
            remaining -= share
        _set_sleeve_cash(
            sleeve, share, note=f"joined the fund — {sleeve.allocation_pct}% of the pool",
        )
        sleeve.initial_capital_usd = share
        sleeve.save(update_fields=["initial_capital_usd", "updated_at"])
        _rebase_sleeve_peak(sleeve, share)
        _sync_link_allocation(sleeve)
    return warnings


def _sync_link_allocation(sleeve: FundSleeve) -> None:
    from apps.brokers.models import StrategyBrokerLink

    StrategyBrokerLink.objects.filter(strategy_id=sleeve.strategy_id, is_active=True).update(
        allocation_usd=sleeve.initial_capital_usd or Decimal("0"), updated_at=timezone.now(),
    )


def _rebase_sleeve_peak(sleeve: FundSleeve, equity: Decimal | None) -> None:
    ap = StrategyAutopilot.objects.filter(strategy_id=sleeve.strategy_id).first()
    if ap is None:
        return
    ap.peak_equity_usd = equity if (equity is not None and equity > 0) else None
    ap.save(update_fields=["peak_equity_usd", "updated_at"])


def _set_sleeve_cash(sleeve: FundSleeve, target: Decimal, *, note: str) -> None:
    """Move the sleeve's cash to ``target`` with a deposit/withdrawal ledger row
    — an EXTERNAL flow, so the TWR history books it as capital, not return."""
    book = Portfolio.objects.select_for_update().get(pk=sleeve.portfolio_id)
    target = Decimal(target).quantize(CENT)
    delta = target - Decimal(str(book.cash_balance or 0))
    if delta == 0:
        return
    book.cash_balance = target
    book.save(update_fields=["cash_balance"])
    LedgerEntry.objects.create(
        portfolio=book,
        kind=LedgerEntry.KIND_DEPOSIT if delta > 0 else LedgerEntry.KIND_WITHDRAWAL,
        ticker="", quantity_delta=Decimal("0"), price=None,
        cash_delta=delta, realized_pnl=Decimal("0"), quantity_after=None,
        cash_balance_after=target, note=f"fund sleeve: {note}",
    )


def _wipe_sleeve_positions(sleeve: FundSleeve, *, note: str) -> int:
    """Drop stale attributed positions (the account is flat, so they cannot be
    real) with reconciliation ledger rows. Returns the count removed."""
    n = 0
    book = sleeve.portfolio
    for pos in list(book.positions.all()):
        LedgerEntry.objects.create(
            portfolio=book, kind=LedgerEntry.KIND_RECONCILE, ticker=pos.ticker,
            quantity_delta=-pos.quantity, price=pos.avg_cost, cash_delta=Decimal("0"),
            realized_pnl=Decimal("0"), quantity_after=Decimal("0"),
            cash_balance_after=book.cash_balance, note=f"fund sleeve: {note}",
        )
        pos.delete()
        n += 1
    return n


# ---------------------------------------------------------------------------
# Account, reset, flatten
# ---------------------------------------------------------------------------
def configure_account(fund: AutonomousFund, account) -> dict:
    """Choose (or change) the fund's shared paper account. Changing it while any
    sleeve still holds positions is refused — flatten first. Sleeve cash is
    meaningless against a new account, so it is zeroed (Reset re-funds)."""
    from apps.brokers.models import BrokerAccount

    if account.user_id != fund.owner_id:
        raise FundError("broker account not found", status=404)
    if account.mode != BrokerAccount.MODE_PAPER:
        raise FundError("the autonomous fund is paper-only — pick a paper account", status=400)
    if fund.broker_account_id == account.id:
        for sleeve in fund.active_sleeves():
            bind_member(fund, sleeve)
        return {"changed": False, "account_id": account.id}

    held = [
        sl.strategy.name for sl in fund.active_sleeves() if sl.portfolio.positions.exists()
    ]
    if held and fund.broker_account_id is not None:
        raise FundError(
            "sleeves still hold positions on the current account — flatten the fund first.",
            status=409, blocking=held,
        )
    with transaction.atomic():
        fund.broker_account = account
        fund.peak_equity_usd = None  # cold re-seed against the new account
        fund.save(update_fields=["broker_account", "peak_equity_usd", "updated_at"])
        for sleeve in fund.active_sleeves():
            if sleeve.portfolio.cash_balance:
                _set_sleeve_cash(sleeve, Decimal("0"), note="fund account changed")
            sleeve.initial_capital_usd = Decimal("0")
            sleeve.save(update_fields=["initial_capital_usd", "updated_at"])
            bind_member(fund, sleeve)
            _rebase_sleeve_peak(sleeve, None)
    return {"changed": True, "account_id": account.id}


def inflight_orders(fund: AutonomousFund):
    """Orders on the fund account that have not settled yet (held for the open,
    or live at the broker)."""
    from apps.brokers.models import BrokerOrder

    if fund.broker_account_id is None:
        return BrokerOrder.objects.none()
    return BrokerOrder.objects.filter(
        broker_account_id=fund.broker_account_id,
        status__in=(
            BrokerOrder.STATUS_PENDING_OPEN, BrokerOrder.STATUS_CONFIRMED,
            BrokerOrder.STATUS_SUBMITTED, BrokerOrder.STATUS_PARTIAL,
        ),
    )


def reset_readiness(fund: AutonomousFund) -> dict:
    """Can the fund be reset right now? Requires a configured account whose book
    is FLAT (no positions, no in-flight orders)."""
    book = account_book(fund)
    if book is None:
        return {"ready": False, "reason": "no_account", "positions": 0, "inflight_orders": 0}
    n_pos = book.positions.count()
    n_inflight = inflight_orders(fund).count()
    if n_pos:
        reason = "positions"
    elif n_inflight:
        reason = "inflight_orders"
    elif not fund.sleeves.filter(is_active=True).exists():
        reason = "no_members"
    else:
        reason = None
    return {
        "ready": reason is None, "reason": reason,
        "positions": n_pos, "inflight_orders": n_inflight,
    }


def reset_fund(fund: AutonomousFund) -> dict:
    """Fresh start: split the account's cash between the active sleeves by
    allocation %, wipe stale attribution, rebase every drawdown peak, clear the
    fund halt. Refused unless the account book is flat (see reset_readiness) —
    resetting over open positions would orphan them into 'unallocated' and let
    the sleeves buy the same exposure again."""
    ready = reset_readiness(fund)
    if not ready["ready"]:
        msg = {
            "no_account": "choose the fund's paper account first.",
            "positions": (
                f"the account still holds {ready['positions']} position(s) — flatten first."
            ),
            "inflight_orders": (
                f"{ready['inflight_orders']} order(s) are still in flight — "
                "wait for them to settle."
            ),
            "no_members": "add at least one strategy first.",
        }[ready["reason"]]
        raise FundError(f"fund is not ready to reset: {msg}", status=409, **ready)

    from .snapshots import record_snapshot

    book = account_book(fund)
    cash = Decimal(str(book.cash_balance or 0)).quantize(CENT)
    sleeves = list(fund.active_sleeves())
    total_pct = sum((Decimal(sl.allocation_pct) for sl in sleeves), Decimal("0"))
    out = {"account_cash": str(cash), "sleeves": [], "wiped_positions": 0}
    with transaction.atomic():
        remaining = cash
        for i, sleeve in enumerate(sleeves):
            if total_pct <= 0:
                share = Decimal("0")
            elif i == len(sleeves) - 1:
                share = remaining
            else:
                share = (cash * Decimal(sleeve.allocation_pct) / total_pct).quantize(CENT)
                remaining -= share
            out["wiped_positions"] += _wipe_sleeve_positions(sleeve, note="fund reset")
            _set_sleeve_cash(
                sleeve, share, note=f"fund reset — {sleeve.allocation_pct}% of ${cash}",
            )
            sleeve.initial_capital_usd = share
            sleeve.save(update_fields=["initial_capital_usd", "updated_at"])
            _sync_link_allocation(sleeve)
            bind_member(fund, sleeve)
            ap = StrategyAutopilot.objects.filter(strategy_id=sleeve.strategy_id).first()
            if ap is not None:
                ap.peak_equity_usd = share if share > 0 else None
                ap.state = StrategyAutopilot.STATE_ACTIVE
                ap.reschedule()
                ap.save(update_fields=["peak_equity_usd", "state", "next_run_at", "updated_at"])
            record_snapshot(sleeve.portfolio, equity=share, cash=share, source="manual")
            out["sleeves"].append({
                "strategy_id": sleeve.strategy_id, "name": sleeve.strategy.name,
                "allocation_pct": str(sleeve.allocation_pct), "capital": str(share),
            })
        # Members that left while still holding positions are flat now too —
        # their leftovers are stale attribution; return everything to the pool.
        for sleeve in fund.sleeves.filter(is_active=False).select_related("portfolio"):
            out["wiped_positions"] += _wipe_sleeve_positions(sleeve, note="fund reset")
            _set_sleeve_cash(sleeve, Decimal("0"), note="fund reset — inactive sleeve cleared")
        fund.state = AutonomousFund.STATE_ACTIVE
        fund.peak_equity_usd = cash if cash > 0 else None
        fund.save(update_fields=["state", "peak_equity_usd", "updated_at"])
        record_snapshot(book, equity=cash, cash=cash, source="manual")
    log.warning("fund reset fund=%s cash=%s sleeves=%s", fund.pk, cash, len(sleeves))
    return out


def _close_side(quantity: Decimal) -> str:
    return "sell" if quantity > 0 else "buy"


def cancel_pending_open(fund: AutonomousFund, *, reason: str = "manual_flatten") -> list[dict]:
    """Cancel the account's locally-held ``pending_open`` orders.

    "Flatten" used to ignore them: with the market closed the previous cycle's
    batch is still held, so flatten reported "0 orders", the account looked flat
    — and at the open the held batch deployed the book anyway (then Reset was
    refused for "positions"). A held order has never reached the venue, so
    cancelling is a local status write; the audit note says who cancelled it.
    """
    from apps.brokers.models import BrokerOrder

    if fund.broker_account_id is None:
        return []
    held = list(
        BrokerOrder.objects.filter(
            broker_account_id=fund.broker_account_id,
            status=BrokerOrder.STATUS_PENDING_OPEN,
        ).order_by("id")
    )
    if not held:
        return []
    note = f"cancelled by fund flatten ({reason}) before it could release at the open"
    BrokerOrder.objects.filter(pk__in=[o.pk for o in held]).update(
        status=BrokerOrder.STATUS_CANCELLED, release_after=None, error_message=note[:500],
    )
    records = [
        {"order_id": o.pk, "ticker": o.ticker, "side": o.side, "quantity": str(o.quantity)}
        for o in held
    ]
    for order in held:
        try:
            run = order.autopilot_runs.order_by("-fire_time_utc").first()
            if run is None:
                continue
            actions = dict(run.guardrail_actions or {})
            actions["flatten_cancelled"] = [
                *actions.get("flatten_cancelled", []),
                {"order_id": order.pk, "ticker": order.ticker, "reason": reason},
            ]
            run.guardrail_actions = actions
            run.save(update_fields=["guardrail_actions"])
        except Exception:  # noqa: BLE001 — the audit must never block a flatten
            log.exception("flatten cancel audit failed order=%s", order.pk)
    log.warning("fund flatten cancelled %s held order(s) fund=%s", len(held), fund.pk)
    return records


def flatten_fund(fund: AutonomousFund, *, reason: str = "manual") -> dict:
    """Queue closing orders for EVERY position in the account book, attributed
    per sleeve where the sleeves account for it (so their ledgers flatten too)
    and untagged for the unattributed residual. Same gated, idempotent order
    path as the cycle bridge; market closed ⇒ held for the open.

    Any of the cycle's own orders still held ``pending_open`` are CANCELLED
    first — otherwise they release at the open and re-deploy the book that was
    just flattened."""
    book = account_book(fund)
    if book is None:
        raise FundError("choose the fund's paper account first.", status=409)

    account = fund.broker_account
    cancelled = cancel_pending_open(fund, reason=reason)
    inflight = {
        o.ticker.upper() for o in inflight_orders(fund)
        if o.client_order_id.startswith("flat-")
    }
    placed = 0
    skipped: list[str] = []
    for pos in book.positions.all().order_by("ticker"):
        ticker = pos.ticker.upper()
        if ticker in inflight:
            skipped.append(ticker)
            continue
        remaining = Decimal(str(pos.quantity))
        # Attribute to sleeves first (same sign only), then the residual.
        for sleeve in list(fund.sleeves.select_related("portfolio", "strategy")):
            spos = sleeve.portfolio.positions.filter(ticker__iexact=ticker).first()
            if spos is None or remaining == 0:
                continue
            sq = Decimal(str(spos.quantity))
            if (sq > 0) != (remaining > 0):
                continue
            qty = min(abs(sq), abs(remaining))
            if _emit_close(account, fund, sleeve, ticker, _close_side(sq), qty):
                placed += 1
            remaining -= qty if remaining > 0 else -qty
        if remaining != 0:
            if _emit_close(account, fund, None, ticker, _close_side(remaining), abs(remaining)):
                placed += 1
    log.warning(
        "fund flatten fund=%s reason=%s orders=%s skipped=%s cancelled=%s",
        fund.pk, reason, placed, skipped, len(cancelled),
    )
    return {
        "orders": placed, "skipped_inflight": skipped,
        "cancelled_pending_open": cancelled,
    }


def flatten_sleeve(sleeve: FundSleeve, *, reason: str = "manual") -> dict:
    """Queue closing orders for ONE sleeve's positions (a member leaving)."""
    fund = sleeve.fund
    account = fund.broker_account
    if account is None:
        raise FundError("choose the fund's paper account first.", status=409)
    placed = 0
    for pos in sleeve.portfolio.positions.all().order_by("ticker"):
        q = Decimal(str(pos.quantity))
        if q == 0:
            continue
        if _emit_close(account, fund, sleeve, pos.ticker.upper(), _close_side(q), abs(q)):
            placed += 1
    log.warning("sleeve flatten sleeve=%s reason=%s orders=%s", sleeve.pk, reason, placed)
    return {"orders": placed}


def _emit_close(account, fund, sleeve, ticker: str, side: str, qty: Decimal) -> bool:
    """One closing order through the bridge's shared emit path. Whole shares on
    the sell side of a short-capable venue are handled by the venue quantity
    rule inside the bridge for cycle orders; closes send the exact held
    quantity (Alpaca accepts fractional sells of a long)."""
    from apps.brokers.capabilities import AUTH_NONE, get_capabilities
    from apps.brokers.market_calendar import next_open

    from . import autopilot, autopilot_risk

    qty = Decimal(qty).quantize(Decimal("0.000001"))
    if qty <= 0:
        return False
    cap = get_capabilities(account.broker)
    is_demo = cap is not None and cap.auth_kind == AUTH_NONE
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    tag = f"s{sleeve.id}" if sleeve is not None else "acct"
    cid = f"flat-f{fund.id}-{tag}-{ticker}-{stamp}"
    strategy = sleeve.strategy if sleeve is not None else None
    book = sleeve.portfolio if sleeve is not None else account.portfolio
    risk_check = (
        autopilot_risk.make_risk_check(strategy, book=book) if strategy is not None
        else (lambda o: [])
    )
    try:
        autopilot._emit_one(
            account=account, user=fund.owner, client_order_id=cid, ticker=ticker,
            broker_side=side, quantity=qty, is_demo=is_demo,
            market_closed=autopilot._market_closed_for(account),
            risk_check=risk_check, next_open_fn=next_open, sleeve=sleeve,
        )
        return True
    except Exception:  # noqa: BLE001 — one bad close can't abort the liquidation
        log.exception("flatten emit failed fund=%s ticker=%s", fund.pk, ticker)
        return False


# ---------------------------------------------------------------------------
# Reporting helpers (fund_overview)
# ---------------------------------------------------------------------------
def attribution_gap(fund: AutonomousFund) -> dict:
    """Σ sleeves vs the account book — the unattributed residual, per ticker and
    in cash. Empty deltas = every position/dollar is owned by some sleeve."""
    book = account_book(fund)
    if book is None:
        return {"available": False, "positions": {}, "cash": None}
    acct = {p.ticker.upper(): Decimal(str(p.quantity)) for p in book.positions.all()}
    attributed: dict[str, Decimal] = {}
    cash = Decimal("0")
    for sleeve in fund.sleeves.select_related("portfolio"):
        cash += Decimal(str(sleeve.portfolio.cash_balance or 0))
        for p in sleeve.portfolio.positions.all():
            t = p.ticker.upper()
            attributed[t] = attributed.get(t, Decimal("0")) + Decimal(str(p.quantity))
    deltas = {}
    for t in set(acct) | set(attributed):
        d = acct.get(t, Decimal("0")) - attributed.get(t, Decimal("0"))
        if abs(d) > Decimal("0.000001"):
            deltas[t] = str(d.normalize())
    return {
        "available": True,
        "positions": deltas,
        "cash": str((Decimal(str(book.cash_balance or 0)) - cash).quantize(CENT)),
    }
