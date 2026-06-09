"""Backfill ``CorporateAction`` cash-dividend rows from FMP.

Without these rows the backtest engine's ``actions_on`` legacy fallback only
infers splits, so backtests credit **zero** dividend cash and run *price-only*
(bonds/dividend payers look like persistent losers — e.g. TLT price-only −5%
vs total-return +85% over 2007-2026). Populating the rows lets the existing
``apply_dividend`` machinery credit dividend cash, making backtests total-return.
Raw OHLC is untouched, so fills stay at actual traded prices (no double-count).

Idempotent: ``bulk_create(ignore_conflicts=True)`` against the
(ticker, as_of_date, kind, source) unique constraint.

    python manage.py backfill_dividends                 # all DailyBar tickers
    python manage.py backfill_dividends --tickers SPY TLT
"""
from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand

from apps.data.models import CorporateAction, DailyBar
from apps.data.providers.factory import get_fmp_provider


class Command(BaseCommand):
    help = "Backfill cash-dividend CorporateAction rows from FMP (makes backtests total-return)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tickers", nargs="*", default=None, help="Default: all DailyBar tickers"
        )
        parser.add_argument("--start", default="2006-01-01")
        parser.add_argument("--end", default=None, help="Default: today")

    def handle(self, *args, **opts) -> None:
        start = dt.date.fromisoformat(opts["start"])
        end = dt.date.fromisoformat(opts["end"]) if opts["end"] else dt.date.today()
        tickers = opts["tickers"] or list(
            # .order_by() clears Meta.ordering so .distinct() collapses to one row
            # per ticker (else ordering columns defeat distinct).
            DailyBar.objects.order_by().values_list("ticker", flat=True).distinct()
        )
        # BYOK in production; in dev/ops the platform key resolves via
        # ALLOW_PLATFORM_DATA_KEYS (paid-provider force_platform is guard-banned).
        provider = get_fmp_provider()

        before = CorporateAction.objects.filter(kind=CorporateAction.CASH_DIVIDEND).count()
        attempted = 0
        for ticker in sorted({t.upper() for t in tickers}):
            try:
                divs = provider.get_dividends(ticker, start, end)
            except Exception as exc:  # noqa: BLE001 — one bad ticker shouldn't abort the sweep
                self.stderr.write(f"{ticker}: fetch failed: {exc}")
                continue
            objs = [
                CorporateAction(
                    ticker=ticker, as_of_date=d, kind=CorporateAction.CASH_DIVIDEND,
                    amount=amt, source="fmp",
                )
                for d, amt in divs
            ]
            CorporateAction.objects.bulk_create(objs, ignore_conflicts=True)
            attempted += len(objs)
            self.stdout.write(f"{ticker}: {len(objs)} dividends")

        after = CorporateAction.objects.filter(kind=CorporateAction.CASH_DIVIDEND).count()
        self.stdout.write(self.style.SUCCESS(
            f"Done. {attempted} dividend rows fetched; {after - before} new ({after} total)."
        ))
