"""Rebuild every leaderboard scorecard under the current metrics version.

Idempotent and safe to run in production: each ``recompute_*`` deletes only its
own ``as_of`` rows before rewriting them, inside one transaction, so re-running
converges and a failure leaves the previous day's rows untouched.

    python manage.py recompute_leaderboard
    python manage.py recompute_leaderboard --as-of 2026-09-07
    python manage.py recompute_leaderboard --check     # report staleness only

``--check`` exits 1 when any row is still on an older ``metrics_version`` — use
it as a deploy gate after the 0006 data migration has marked pre-fix rows stale.
"""
from __future__ import annotations

import datetime as dt
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.leaderboard.compute import recompute_all
from apps.leaderboard.forward_returns import DEFAULT_FORWARD_DAYS
from apps.leaderboard.models import (
    METRICS_VERSION,
    AgentScorecard,
    StrategyScorecard,
)


def _stale_counts() -> dict[str, int]:
    return {
        "strategies": StrategyScorecard.objects.filter(
            metrics_version__lt=METRICS_VERSION
        ).count(),
        "agents": AgentScorecard.objects.filter(
            metrics_version__lt=METRICS_VERSION
        ).count(),
    }


class Command(BaseCommand):
    help = "Recompute leaderboard scorecards (agents, models, strategies)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--as-of",
            dest="as_of",
            default=None,
            help="Date to stamp the rebuilt rows with (YYYY-MM-DD). Default: today.",
        )
        parser.add_argument(
            "--forward-days",
            dest="forward_days",
            type=int,
            default=DEFAULT_FORWARD_DAYS,
            help=f"Forward-return horizon for agent scoring (default {DEFAULT_FORWARD_DAYS}).",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Report how many rows are below the current metrics version and exit.",
        )

    def handle(self, *args, **options):
        stale = _stale_counts()
        if options["check"]:
            self.stdout.write(json.dumps({
                "metrics_version": METRICS_VERSION, "stale": stale,
            }))
            if stale["strategies"] or stale["agents"]:
                raise CommandError(
                    "leaderboard rows are stale — run `manage.py recompute_leaderboard`"
                )
            return

        raw = options.get("as_of")
        if raw:
            try:
                as_of = dt.date.fromisoformat(raw)
            except ValueError as exc:
                raise CommandError(f"--as-of must be YYYY-MM-DD, got {raw!r}") from exc
        else:
            as_of = timezone.localdate()

        with transaction.atomic():
            result = recompute_all(as_of, options["forward_days"])
        result["stale_before"] = stale
        result["stale_after"] = _stale_counts()
        self.stdout.write(json.dumps(result))
        return None
