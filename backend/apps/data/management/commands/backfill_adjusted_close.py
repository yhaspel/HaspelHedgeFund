"""Backfill ``DailyBar.adjusted_close`` with TRUE dividend-adjusted closes.

FMP's `/historical-price-eod/full` endpoint returns `adjClose == close` (split-
but not dividend-adjusted), so historically every row was stored price-only.
Total-return consumers that read ``adjusted_close`` (vol/beta/pairs sizing,
leaderboard forward-returns, the Markov regime classifier) were therefore
silently price-only. This re-fetches the dividend-adjusted series and updates
``adjusted_close`` on existing rows. Raw OHLC (``close``) is left untouched.

    python manage.py backfill_adjusted_close                 # all DailyBar tickers
    python manage.py backfill_adjusted_close --tickers SPY TLT
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.data.models import DailyBar
from apps.data.providers.factory import get_fmp_provider

SOURCE = "fmp"


class Command(BaseCommand):
    help = "Backfill DailyBar.adjusted_close with true dividend-adjusted closes from FMP."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tickers", nargs="*", default=None, help="Default: all DailyBar tickers"
        )

    def handle(self, *args, **opts) -> None:
        tickers = opts["tickers"] or list(
            DailyBar.objects.order_by().values_list("ticker", flat=True).distinct()
        )
        # BYOK in production; in dev/ops the platform key resolves via
        # ALLOW_PLATFORM_DATA_KEYS (paid-provider force_platform is guard-banned).
        provider = get_fmp_provider()
        total_updated = 0
        for ticker in sorted({t.upper() for t in tickers}):
            bars = list(DailyBar.objects.filter(ticker=ticker, source=SOURCE).order_by("date"))
            if not bars:
                continue
            lo, hi = bars[0].date, bars[-1].date
            try:
                adj = provider.get_adjusted_closes(ticker, lo, hi)
            except Exception as exc:  # noqa: BLE001 — one bad ticker shouldn't abort the sweep
                self.stderr.write(f"{ticker}: fetch failed: {exc}")
                continue
            to_update = []
            for b in bars:
                a = adj.get(b.date.isoformat())
                if a is None:
                    continue
                new = Decimal(str(a))
                if abs(float(b.adjusted_close) - float(new)) > 1e-6:
                    b.adjusted_close = new
                    to_update.append(b)
            if to_update:
                DailyBar.objects.bulk_update(to_update, ["adjusted_close"], batch_size=2000)
            total_updated += len(to_update)
            self.stdout.write(f"{ticker}: {len(to_update)}/{len(bars)} rows updated")
        self.stdout.write(
            self.style.SUCCESS(f"Done. {total_updated} adjusted_close values corrected.")
        )
