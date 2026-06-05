"""Seed passing validation backtests so the fund's autopilots can be enabled.

Educational/paper helper: writes a clearly-labelled ``[seed]`` passing backtest
per strategy (see ``apps.backtests.seed``) so the §9 enable gate opens out of the
box. Targets an AutonomousFund's member strategies by owner email, or a single
strategy by id. Idempotent unless ``--force``.

Usage:
    uv run python manage.py seed_validation_backtests --user me@example.com
    uv run python manage.py seed_validation_backtests --user me@example.com --fund "Autonomous Fund"
    uv run python manage.py seed_validation_backtests --strategy 46
    uv run python manage.py seed_validation_backtests --user me@example.com --dry-run
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.backtests.seed import has_passing_backtest, seed_validation_backtest
from apps.portfolios.models import AutonomousFund, PortfolioStrategy

User = get_user_model()


class Command(BaseCommand):
    help = "Seed passing validation backtests so autopilots can be enabled (demo/paper)."

    def add_arguments(self, parser):
        parser.add_argument("--user", default="", help="Owner email (targets fund strategies).")
        parser.add_argument("--fund", default="", help="Limit to this AutonomousFund name.")
        parser.add_argument("--strategy", type=int, default=None, help="Seed a single strategy id.")
        parser.add_argument(
            "--force", action="store_true",
            help="Seed even if a qualifying backtest already exists.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be seeded without writing.",
        )

    def handle(self, *args, **opts):
        strategies = self._resolve_strategies(opts)
        if not strategies:
            raise CommandError("no matching strategies — pass --user <email> or --strategy <id>.")

        dry = opts["dry_run"]
        force = opts["force"]
        seeded = skipped = 0
        for s in strategies:
            already = has_passing_backtest(s)
            if already and not force:
                self.stdout.write(f"  strategy #{s.id} '{s.name}': already validated — skipping.")
                skipped += 1
                continue
            if dry:
                self.stdout.write(self.style.WARNING(
                    f"  strategy #{s.id} '{s.name}': would seed a passing backtest."
                ))
                seeded += 1
                continue
            bt = seed_validation_backtest(s, force=force)
            if bt is None:
                skipped += 1
                continue
            self.stdout.write(self.style.SUCCESS(
                f"  strategy #{s.id} '{s.name}': seeded backtest #{bt.id} → gate now passes."
            ))
            seeded += 1

        verb = "would seed" if dry else "seeded"
        self.stdout.write(self.style.SUCCESS(
            f"Done: {verb} {seeded}, skipped {skipped}. "
            "Open each strategy's Autopilot page to enable."
        ))

    def _resolve_strategies(self, opts) -> list[PortfolioStrategy]:
        if opts["strategy"] is not None:
            s = PortfolioStrategy.objects.filter(pk=opts["strategy"]).first()
            if s is None:
                raise CommandError(f"no strategy with id {opts['strategy']}.")
            return [s]

        email = (opts["user"] or "").strip()
        if not email:
            raise CommandError("pass --user <email> or --strategy <id>.")
        owner = User.objects.filter(email__iexact=email).first()
        if owner is None:
            raise CommandError(f"no User with email {email!r}.")

        funds = AutonomousFund.objects.filter(owner=owner)
        if opts["fund"]:
            funds = funds.filter(name=opts["fund"])
        seen: dict[int, PortfolioStrategy] = {}
        for fund in funds:
            for s in fund.strategies.select_related("autopilot").all():
                seen[s.id] = s
        return list(seen.values())
