"""P10 §B1/§B2 — re-true historical backtest baselines + benchmark blocks.

``baseline_curve`` was fixed to a TOTAL-RETURN, true equal-weight basket
(adjusted_close, chained per-ticker returns); historical ``done`` records still
carry the old price-only, price-weighted ``baseline_return_pct``. This command
recomputes it — **preserving the old value first** into
``baseline_return_pct_legacy`` (write-once: a re-run never overwrites the
preserved original) so the rewrite is reversible and §9-gate history stays
reconstructible. It also (re)computes the §B2 ``benchmarks`` block (SPY-TR /
QQQ-TR beta / CAPM alpha / IR) for each record.

Expect "vs baseline" deltas to shrink or flip — that is the point: the old
baseline understated a dividend-credited benchmark by hundreds of pp over long
windows.

Usage:
    uv run python manage.py recompute_baselines              # all done backtests
    uv run python manage.py recompute_baselines --ids 56 57 60
    uv run python manage.py recompute_baselines --dry-run
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.backtests.metrics import baseline_curve, benchmark_stats, stitched_oos_returns
from apps.backtests.models import Backtest, BacktestMetrics


class Command(BaseCommand):
    help = ("Recompute baseline_return_pct (TR equal-weight) + the SPY/QQQ benchmark "
            "block for done backtests, preserving the legacy value first.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--ids", nargs="*", type=int, default=None,
            help="Limit to these backtest ids (default: every done backtest).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print old → new values without writing.",
        )

    def handle(self, *args, **opts):
        qs = Backtest.objects.filter(status=Backtest.DONE).order_by("id")
        if opts["ids"]:
            qs = qs.filter(id__in=opts["ids"])
        dry = opts["dry_run"]
        updated = skipped = 0
        for bt in qs.iterator():
            metrics = BacktestMetrics.objects.filter(backtest=bt).first()
            if metrics is None:
                self.stdout.write(f"  bt#{bt.id}: no metrics row — skipping.")
                skipped += 1
                continue
            dates, equity, _ = stitched_oos_returns(bt)
            if len(dates) < 2:
                self.stdout.write(f"  bt#{bt.id}: no stitched OOS days — skipping.")
                skipped += 1
                continue
            bl = baseline_curve(bt, dates)
            bl_ret = (bl[-1] / bl[0] - 1.0) if len(bl) >= 2 and bl[0] else 0.0
            new_val = Decimal(str(round(bl_ret * 100, 4)))
            old_val = metrics.baseline_return_pct
            bench = benchmark_stats(bt, dates, equity)
            self.stdout.write(
                f"  bt#{bt.id} '{bt.name}': baseline {old_val}% → {new_val}%"
                + (f" | SPY α {bench['SPY'].get('alpha_annual_pct')}%/yr "
                   f"β {bench['SPY'].get('beta')}" if "SPY" in bench else "")
            )
            if dry:
                updated += 1
                continue
            # Preserve the pre-rewrite value exactly once; a re-run keeps the
            # ORIGINAL legacy value, not an intermediate one.
            if metrics.baseline_return_pct_legacy is None:
                metrics.baseline_return_pct_legacy = old_val
            metrics.baseline_return_pct = new_val
            metrics.benchmarks = bench
            metrics.save(update_fields=[
                "baseline_return_pct", "baseline_return_pct_legacy", "benchmarks",
            ])
            updated += 1
        verb = "would update" if dry else "updated"
        self.stdout.write(self.style.SUCCESS(
            f"Done: {verb} {updated}, skipped {skipped}."
        ))
