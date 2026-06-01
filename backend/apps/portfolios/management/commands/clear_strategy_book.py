"""P4 cleanup: flatten positions out of a strategy book.

Use this to remove legacy/foreign positions that ended up in a strategy's
book — e.g. the sector-ETF positions that co-mingled into the shared
"Default paper portfolio" before strategy books were isolated. Each position
is liquidated at its latest mark (long → sell, short → cover), cash is
credited/debited accordingly, a KIND_RECONCILE ledger entry is written for
the audit trail, and the Position row is removed. Nothing is hard-deleted
without a ledger record.

Dry-run by default; pass --apply to mutate. Target by strategy or portfolio,
and optionally limit to specific tickers.

Usage:
    uv run python manage.py clear_strategy_book --strategy 1
    uv run python manage.py clear_strategy_book --strategy 1 --tickers SOXX,TAN,XLE,XLI,XLP --apply
    uv run python manage.py clear_strategy_book --portfolio 1 --apply
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.portfolios.models import LedgerEntry, Portfolio, PortfolioStrategy, Position


def _money(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = (
        "Flatten (liquidate at mark) positions out of a strategy book, writing "
        "a reconciliation ledger entry per position. Dry-run unless --apply."
    )

    def add_arguments(self, parser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--strategy", type=int, help="Strategy id whose book to clear.")
        group.add_argument("--portfolio", type=int, help="Portfolio id to clear directly.")
        parser.add_argument(
            "--tickers", type=str, default="",
            help="Comma-separated tickers to clear (default: all positions in the book).",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually liquidate + write ledger. Omit for a dry run.",
        )

    def handle(self, *args, **opts) -> None:
        apply = bool(opts["apply"])
        if opts.get("strategy") is not None:
            try:
                strategy = PortfolioStrategy.objects.select_related("portfolio").get(
                    pk=opts["strategy"]
                )
            except PortfolioStrategy.DoesNotExist as exc:
                raise CommandError(f"strategy {opts['strategy']} not found") from exc
            portfolio = strategy.portfolio
            if portfolio is None:
                self.stdout.write(self.style.SUCCESS("strategy has no book — nothing to clear."))
                return
        else:
            try:
                portfolio = Portfolio.objects.get(pk=opts["portfolio"])
            except Portfolio.DoesNotExist as exc:
                raise CommandError(f"portfolio {opts['portfolio']} not found") from exc

        if portfolio.kind != Portfolio.KIND_STRATEGY:
            raise CommandError(
                f"portfolio {portfolio.id} is kind={portfolio.kind}; this command only "
                "clears strategy books (never the Manual Book or a broker book)."
            )

        wanted = {t.strip().upper() for t in opts["tickers"].split(",") if t.strip()}
        positions = list(Position.objects.filter(portfolio=portfolio))
        if wanted:
            positions = [p for p in positions if p.ticker.upper() in wanted]
        if not positions:
            self.stdout.write(self.style.SUCCESS("No matching positions — nothing to clear."))
            return

        from apps.portfolios.valuation import get_mark

        self.stdout.write(
            f"Portfolio {portfolio.id} '{portfolio.name}' — clearing "
            f"{len(positions)} position(s). Cash before: ${_money(portfolio.cash_balance)}"
        )

        def _mark(ticker: str, fallback: Decimal) -> Decimal:
            try:
                m = get_mark(ticker, user=portfolio.user)
            except Exception:
                m = None
            if m is not None and getattr(m, "price", None) and m.price > 0:
                return Decimal(str(m.price))
            return fallback

        total_cash_delta = Decimal("0")
        for pos in positions:
            price = _mark(pos.ticker, pos.avg_cost)
            qty = pos.quantity
            if qty >= 0:  # long → sell
                cash_delta = _money(qty * price)
                realized = _money((price - pos.avg_cost) * qty)
            else:          # short → buy to cover
                cash_delta = _money(qty * price)  # qty<0 → negative cash_delta (pay to cover)
                realized = _money((pos.avg_cost - price) * qty.copy_abs())
            total_cash_delta += cash_delta
            self.stdout.write(
                f"  {pos.ticker:<6} qty {qty} @ {price}  →  cash {'+' if cash_delta >= 0 else ''}"
                f"{cash_delta}  realized {realized}"
            )
            if apply:
                with transaction.atomic():
                    locked = Portfolio.objects.select_for_update().get(pk=portfolio.pk)
                    locked.cash_balance = _money(locked.cash_balance + cash_delta)
                    locked.save(update_fields=["cash_balance"])
                    LedgerEntry.objects.create(
                        portfolio=locked,
                        kind=LedgerEntry.KIND_RECONCILE,
                        ticker=pos.ticker,
                        quantity_delta=-qty,
                        price=price,
                        cash_delta=cash_delta,
                        realized_pnl=realized,
                        quantity_after=Decimal("0"),
                        cash_balance_after=locked.cash_balance,
                        position=None,
                        note="cleanup: legacy co-mingled position flattened to cash",
                        created_by=portfolio.user,
                    )
                    pos.delete()
                    portfolio = locked

        if apply:
            portfolio.refresh_from_db()
            self.stdout.write(self.style.SUCCESS(
                f"Applied. Cleared {len(positions)} position(s). "
                f"Cash after: ${_money(portfolio.cash_balance)}"
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN — would clear {len(positions)} position(s); net cash change "
                f"${_money(total_cash_delta)}. Re-run with --apply to execute."
            ))
