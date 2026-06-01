"""P4 fix: split strategy books shared by multiple strategies.

Historically every new strategy defaulted onto one shared "Default paper
portfolio", so dozens of strategies pointed at a single kind="strategy"
Portfolio. That made the Portfolios hub collapse them into one row and — far
worse — let enrollment of one strategy close another strategy's positions
(enrollment closes any holding not in *this* strategy's target).

This command gives each shared strategy its own dedicated book. For every
Portfolio referenced by more than one strategy, the earliest-created strategy
KEEPS the existing book (and all of its current positions / cash / ledger
history, which can no longer be cleanly attributed); each other strategy is
re-pointed at a fresh, empty kind="strategy" book named after it.

Nothing is ever deleted: positions and ledger rows stay where they are. The
only mutation is creating new empty books and re-pointing the FK on the
"mover" strategies.

Usage:
    # Preview only (default) — shows the plan, changes nothing:
    uv run python manage.py split_shared_strategy_books
    # Actually apply:
    uv run python manage.py split_shared_strategy_books --apply
    # Limit to one user:
    uv run python manage.py split_shared_strategy_books --apply --user 1
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.portfolios.models import Portfolio, PortfolioStrategy


class Command(BaseCommand):
    help = (
        "Give each strategy its own book when several strategies share one "
        "kind=strategy portfolio (dry-run unless --apply is passed)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually create books and re-point strategies. Omit for a dry run.",
        )
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="Limit to a single user id (defaults to all users).",
        )

    def handle(self, *args, **opts) -> None:
        apply = bool(opts["apply"])
        user_id = opts["user"]

        strategies = (
            PortfolioStrategy.objects
            .select_related("portfolio")
            .order_by("portfolio_id", "id")
        )
        if user_id is not None:
            strategies = strategies.filter(user_id=user_id)

        by_portfolio: dict[int, list[PortfolioStrategy]] = defaultdict(list)
        for strat in strategies:
            by_portfolio[strat.portfolio_id].append(strat)

        shared = {
            pid: members
            for pid, members in by_portfolio.items()
            if len(members) > 1
            and members[0].portfolio.kind == Portfolio.KIND_STRATEGY
        }

        if not shared:
            self.stdout.write(self.style.SUCCESS("No shared strategy books found — nothing to do."))
            return

        total_moves = 0
        for pid, members in shared.items():
            primary = members[0]            # earliest-created keeps the book
            movers = members[1:]
            self.stdout.write(
                f"Portfolio {pid} '{primary.portfolio.name}' is shared by "
                f"{len(members)} strategies."
            )
            self.stdout.write(
                f"  keep  → strategy {primary.id} '{primary.name}' "
                f"(retains existing positions/cash/ledger)"
            )
            for strat in movers:
                new_name = (strat.name or "Strategy book").strip()[:64] or "Strategy book"
                self.stdout.write(
                    f"  move  → strategy {strat.id} '{strat.name}' "
                    f"to a new empty book '{new_name}'"
                )
                total_moves += 1
                if apply:
                    with transaction.atomic():
                        new_book = Portfolio.objects.create(
                            user_id=strat.user_id,
                            kind=Portfolio.KIND_STRATEGY,
                            name=new_name,
                        )
                        strat.portfolio = new_book
                        strat.save(update_fields=["portfolio"])

        summary = (
            f"{len(shared)} shared book(s); {total_moves} strateg(ies) "
            f"would move to their own book."
        )
        if apply:
            self.stdout.write(self.style.SUCCESS(f"Applied: {summary}"))
        else:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN — {summary} Re-run with --apply to make the changes."
            ))
