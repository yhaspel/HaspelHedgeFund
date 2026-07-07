"""P5-SH WS3.2 — retention pruning for LLMCall / NewsItem rows.

Conservative by design: DRY-RUN by default (reports candidate counts and deletes
nothing); actual deletion requires --commit. Nothing runs on a schedule.

Inventory (2026-07, verified against code):
  * LLMCall stores NO prompt/response text — only token counts, cost, model,
    agent name and a timestamp — so pruning is a pure retention/space decision,
    not a privacy redaction. Run/Backtest.total_cost_usd is denormalized and is
    NOT recomputed from LLMCall, so pruning never changes a run's displayed cost.
    CAVEAT: the leaderboard's per-strategy *lifetime* cost (apps/leaderboard/
    compute.py) sums historical LLMCall rows, so pruning them lowers that figure
    for old strategies. Keep enough history if you rely on it.
  * NewsItem rows (headline/summary/raw_text) are re-fetchable market data, not
    private user data.
"""
from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Prune old LLMCall / NewsItem rows past a configurable age. Dry-run by "
        "default (reports candidates, deletes nothing); pass --commit to delete."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days", type=int, required=True,
            help="Delete rows whose timestamp is older than N days.",
        )
        parser.add_argument(
            "--llm-calls", action="store_true",
            help="Include LLMCall rows (matched on created_at).",
        )
        parser.add_argument(
            "--news", action="store_true",
            help="Include NewsItem rows (matched on published_at).",
        )
        parser.add_argument(
            "--commit", action="store_true",
            help="Actually delete. Without this the command is a dry run.",
        )

    def handle(self, *args, **opts):
        days = opts["older_than_days"]
        if days < 1:
            raise CommandError("--older-than-days must be >= 1")
        if not (opts["llm_calls"] or opts["news"]):
            raise CommandError("Choose at least one target: --llm-calls and/or --news.")

        cutoff = timezone.now() - dt.timedelta(days=days)
        commit = opts["commit"]
        header = "DELETING" if commit else "DRY-RUN (nothing deleted)"
        self.stdout.write(
            f"{header}: rows older than {days}d (before {cutoff.date().isoformat()})"
        )

        total = 0
        if opts["llm_calls"]:
            from hedgefund_agents.models import LLMCall

            total += self._report_or_delete(
                "LLMCall", LLMCall.objects.filter(created_at__lt=cutoff), commit
            )
        if opts["news"]:
            from apps.data.models import NewsItem

            total += self._report_or_delete(
                "NewsItem", NewsItem.objects.filter(published_at__lt=cutoff), commit
            )

        if not commit and total:
            self.stdout.write("Re-run with --commit to delete these rows.")

    def _report_or_delete(self, label: str, qs, commit: bool) -> int:
        n = qs.count()
        if not commit:
            self.stdout.write(f"  {label}: {n} candidate row(s)")
            return n
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f"  {label}: deleted {n} row(s)"))
        return n
