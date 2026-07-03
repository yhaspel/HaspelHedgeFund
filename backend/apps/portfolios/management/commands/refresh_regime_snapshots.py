"""One-off / ops refit of the Markov RegimeSnapshot universe (2026-07-03).

The nightly platform-key prewarm was deleted in the 2026-05-26 BYOK fix;
deterministic pod cycles now refit at cycle start with the owner's provider.
This command is the manual counterpart — populate the fund page's Markov
consensus NOW instead of waiting for the next pod cycle, or backfill after
an outage. BYOK: requires ``--user`` (the fit runs under that user's FMP
key; there is no platform-key path).

    manage.py refresh_regime_snapshots --user you@example.com
    manage.py refresh_regime_snapshots --user you@example.com --as-of 2026-07-03
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Fit + persist Markov regime snapshots for the 16-ETF reference "
        "universe using the given user's data key, then refresh the day's "
        "stored Markov consensus."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--user", required=True,
            help="Email of the user whose BYOK data key funds the bar fetches.",
        )
        parser.add_argument(
            "--as-of", default=None,
            help="ISO date to fit as of (default: today UTC).",
        )

    def handle(self, *args, **options) -> None:
        from apps.data.providers.factory import get_fmp_provider
        from hedgefund_agents.macro.markov_regime import MarkovConfig
        from hedgefund_agents.macro.regime_persistence import (
            ALWAYS_MODELLED_TICKERS,
            fit_and_persist,
            update_stored_consensus,
        )

        user = get_user_model().objects.filter(email=options["user"]).first()
        if user is None:
            raise CommandError(f"No user with email {options['user']!r}.")
        as_of = (
            dt.date.fromisoformat(options["as_of"])
            if options["as_of"] else dt.date.today()
        )
        provider = get_fmp_provider(user=user)

        fitted, failed = [], {}
        for ticker in ALWAYS_MODELLED_TICKERS:
            try:
                fit_and_persist(
                    ticker=ticker, as_of_date=as_of,
                    config=MarkovConfig(), data_provider=provider,
                )
                fitted.append(ticker)
            except Exception as exc:  # noqa: BLE001 — per-ticker, keep going
                failed[ticker] = f"{exc.__class__.__name__}: {exc}"

        consensus = update_stored_consensus(as_of) if fitted else None

        self.stdout.write(
            f"as_of={as_of} fitted={len(fitted)}/{len(ALWAYS_MODELLED_TICKERS)}"
        )
        for t, reason in failed.items():
            self.stdout.write(self.style.WARNING(f"  failed {t}: {reason}"))
        if consensus:
            self.stdout.write(self.style.SUCCESS(
                f"consensus: {consensus['consensus_state']} "
                f"({consensus['available_count']} fresh, "
                f"{consensus['stale_count']} stale)"
            ))
        elif fitted:
            self.stdout.write(
                "consensus not stored — no MacroSnapshot row exists for "
                f"{as_of} (it will pick the fresh rows up when built)."
            )
