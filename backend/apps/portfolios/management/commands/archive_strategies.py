"""P10 §D2 — one-time assisted archive sweep (NOT a standing auto-archive).

Sets ``is_active=False`` on an explicit, operator-confirmed id list. The plan's
candidate list for the 2026-06 sweep (requires owner confirmation at run time):

  * 33 orphan experiments: 1,2,3,4,6,7,8,9,11,12,13,14,15,16,17,18,22,23,24,
    26,27,28,29,30,31,39,40,41,42,43,44,45,52
  * retired council pods 46,47,48 (their backtest evidence stays reachable via
    ?include_archived=1)
  * keep ACTIVE: 53/54/55 (fund pods) and 56 (the news-sentiment lab sleeve).

Why not an auto-rule: recency is useless while the project is weeks old, and
any fund/broker/backtest-link heuristic would mis-archive #56 (intentionally
fund-unlinked). One reviewed sweep + the §D1 archive verb prevents recurrence.

Safety rails: refuses ids that are fund members or have an ACTIVE broker link
or an ENABLED autopilot (archive ≠ stop-trading; disable the autopilot first).

Usage:
    uv run python manage.py archive_strategies --ids 1 2 3 ... [--unarchive] [--dry-run]
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.portfolios.models import PortfolioStrategy


class Command(BaseCommand):
    help = "Archive (is_active=False) an explicit list of strategies — P10 §D2 sweep."

    def add_arguments(self, parser):
        parser.add_argument("--ids", nargs="+", type=int, required=True)
        parser.add_argument("--unarchive", action="store_true",
                            help="Set is_active=True instead.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        ids = opts["ids"]
        rows = list(PortfolioStrategy.objects.filter(id__in=ids))
        found = {s.id for s in rows}
        missing = [i for i in ids if i not in found]
        if missing:
            raise CommandError(f"no such strategy ids: {missing}")

        if not opts["unarchive"]:
            blocked: list[str] = []
            for s in rows:
                if s.funds.exists():
                    blocked.append(f"#{s.id} '{s.name}' is a fund member")
                from apps.brokers.models import StrategyBrokerLink

                if StrategyBrokerLink.objects.filter(strategy=s, is_active=True).exists():
                    blocked.append(f"#{s.id} '{s.name}' has an active broker link")
                ap = getattr(s, "autopilot", None)
                if ap is not None and ap.is_enabled:
                    blocked.append(f"#{s.id} '{s.name}' has an ENABLED autopilot")
            if blocked:
                raise CommandError(
                    "refusing to archive live strategies (disable/unlink first):\n  "
                    + "\n  ".join(blocked)
                )

        target = bool(opts["unarchive"])
        verb = "unarchive" if target else "archive"
        for s in rows:
            self.stdout.write(f"  would {verb}: #{s.id} '{s.name}' (kind={s.kind})"
                              if opts["dry_run"] else
                              f"  {verb}d: #{s.id} '{s.name}' (kind={s.kind})")
        if not opts["dry_run"]:
            PortfolioStrategy.objects.filter(id__in=ids).update(is_active=target)
        self.stdout.write(self.style.SUCCESS(
            f"{'Would update' if opts['dry_run'] else 'Updated'} {len(rows)} strategies "
            f"(is_active={target})."
        ))
