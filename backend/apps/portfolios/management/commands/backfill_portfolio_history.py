"""P10 §C3 — backfill daily NAV snapshots from the broker's own history.

Pulls Alpaca's GET /v2/account/portfolio/history (daily equity since ~account
creation) through the adapter's ``get_portfolio_history`` and upserts
``PortfolioSnapshot`` rows (source=backfill). ``net_flow`` per day is derived
from the book's ledger (deposits / withdrawals / reconciliation adjustments)
so time-weighted returns stay honest across funding events.

``--start`` matters: account 11 inherited retired council-pod history, so its
series should start at the pod-#53 go-live (2026-06-09, per P10 §A6); accounts
12/13 at the 2026-06-04 funding. Existing sweep-written rows for a date are
overwritten only with ``--overwrite`` (the sweep's post-close mark and the
broker's number should agree; keep the sweep's by default).

Usage:
    uv run python manage.py backfill_portfolio_history --account 11 --start 2026-06-09
    uv run python manage.py backfill_portfolio_history --all-fund --user me@example.com
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.brokers.capabilities import get_adapter_factory
from apps.brokers.models import BrokerAccount, StrategyBrokerLink
from apps.portfolios.models import PortfolioSnapshot
from apps.portfolios.snapshots import external_flow, record_snapshot

User = get_user_model()


class Command(BaseCommand):
    help = ("Backfill PortfolioSnapshot rows from broker portfolio history "
            "(Alpaca GET /v2/account/portfolio/history).")

    def add_arguments(self, parser):
        parser.add_argument("--account", type=int, default=None,
                            help="BrokerAccount id to backfill.")
        parser.add_argument("--all-fund", action="store_true",
                            help="Backfill every active fund-linked broker account.")
        parser.add_argument("--user", default="",
                            help="Owner email (required with --all-fund).")
        parser.add_argument("--start", default="",
                            help="Drop points before this date (YYYY-MM-DD) — e.g. the "
                                 "pod go-live, so a reused account's prior regime is "
                                 "excluded from the pod's track record.")
        parser.add_argument("--period", default="1A",
                            help="Alpaca history period (default 1A).")
        parser.add_argument("--overwrite", action="store_true",
                            help="Overwrite snapshot rows that already exist for a date "
                                 "(default keeps existing sweep-written rows).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        accounts = self._resolve_accounts(opts)
        start = (
            dt.date.fromisoformat(opts["start"]) if opts["start"] else None
        )
        total = 0
        for account in accounts:
            total += self._backfill(account, start=start, period=opts["period"],
                                     overwrite=opts["overwrite"], dry=opts["dry_run"])
        verb = "would write" if opts["dry_run"] else "wrote"
        self.stdout.write(self.style.SUCCESS(f"Done: {verb} {total} snapshot rows."))

    def _resolve_accounts(self, opts) -> list[BrokerAccount]:
        if opts["account"] is not None:
            account = BrokerAccount.objects.filter(pk=opts["account"]).first()
            if account is None:
                raise CommandError(f"no BrokerAccount {opts['account']}.")
            return [account]
        if not opts["all_fund"]:
            raise CommandError("pass --account <id> or --all-fund --user <email>.")
        email = (opts["user"] or "").strip()
        if not email:
            raise CommandError("--all-fund requires --user <email>.")
        owner = User.objects.filter(email__iexact=email).first()
        if owner is None:
            raise CommandError(f"no User with email {email!r}.")
        links = StrategyBrokerLink.objects.filter(
            strategy__user=owner, is_active=True,
        ).select_related("broker_account")
        return [link.broker_account for link in links]

    def _backfill(self, account: BrokerAccount, *, start, period, overwrite, dry) -> int:
        if account.portfolio_id is None:
            self.stdout.write(f"  account {account.pk}: no linked portfolio — skipping.")
            return 0
        factory = get_adapter_factory(account.broker)
        if factory is None:
            self.stdout.write(f"  account {account.pk}: unknown broker — skipping.")
            return 0
        adapter = factory(account)
        get_history = getattr(adapter, "get_portfolio_history", None)
        if get_history is None:
            self.stdout.write(
                f"  account {account.pk}: {account.broker} has no portfolio-history "
                "support — skipping."
            )
            return 0
        points = get_history(period=period)
        portfolio = account.portfolio
        existing = set(
            PortfolioSnapshot.objects.filter(portfolio=portfolio)
            .values_list("date", flat=True)
        )
        written = 0
        for when, equity in points:
            on = when.date()
            if start and on < start:
                continue
            if on in existing and not overwrite:
                continue
            flow = external_flow(portfolio, on)
            self.stdout.write(
                f"  account {account.pk} {on}: equity {equity} flow {flow}"
            )
            if not dry:
                record_snapshot(
                    portfolio, equity=equity, on=on, net_flow=flow,
                    source=PortfolioSnapshot.SOURCE_BACKFILL,
                )
            written += 1
        return written
