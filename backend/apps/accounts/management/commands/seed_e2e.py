"""seed_e2e — build the known world for Phase-8 Lane-B (live-smoke) E2E.

Composes the existing seeders so the live `docker-compose` stack has a fixed,
deterministic world the Playwright `@smoke` journeys can drive:

  * a fixed E2E user (``e2e@local`` / known password), JWT-ready;
  * the 3-account autonomous fund + per-strategy autopilots + a passing
    ``[seed]`` validation backtest each (via ``bootstrap_autonomous_fund
    --broker mock --no-verify --seed-validation-backtest``), so ``/fund`` and
    ``/strategies/:id/autopilot`` have a live world (one enabled, others gated);
  * a default watchlist with a few tickers.

Pairs with the ``E2E_STUB_LLM`` / ``E2E_STUB_BROKER`` settings branches so a
triggered run/cycle/order completes instantly and identically — no frontier
models, no real orders. Idempotent. **Refuses to run** unless
``settings.E2E_SEED_ALLOWED`` is True (dev/test settings only).

    DJANGO_SETTINGS_MODULE=hedgefund.settings.dev \
      uv run python manage.py seed_e2e
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()

E2E_EMAIL = "e2e@local"
E2E_PASSWORD = "e2e-password-123"
WATCHLIST_TICKERS = ["AAPL", "MSFT", "NVDA", "AVGO", "SPY"]


class Command(BaseCommand):
    help = "Seed the deterministic Lane-B E2E world (dev/test settings only)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=E2E_EMAIL, help="E2E user email.")
        parser.add_argument("--password", default=E2E_PASSWORD, help="E2E user password.")

    def handle(self, *args, **opts):
        if not getattr(settings, "E2E_SEED_ALLOWED", False):
            raise CommandError(
                "seed_e2e refuses to run: E2E_SEED_ALLOWED is False. "
                "Use the dev/test settings module."
            )

        email = opts["email"]
        password = opts["password"]

        user, created = User.objects.get_or_create(
            email=email, defaults={"is_active": True}
        )
        user.set_password(password)
        user.is_active = True
        user.save()
        self.stdout.write(f"{'created' if created else 'updated'} user {email}")

        # 3-account autonomous fund + autopilots + a passing validation backtest
        # per strategy (mock broker, no live verification).
        call_command(
            "bootstrap_autonomous_fund",
            user=email,
            broker="mock",
            no_verify=True,
            seed_validation_backtest=True,
        )
        self.stdout.write("bootstrapped autonomous fund (mock broker)")

        self._seed_watchlist(user)

        self.stdout.write(self.style.SUCCESS("seed_e2e complete."))

    def _seed_watchlist(self, user) -> None:
        from apps.watchlists.models import Watchlist, WatchlistTicker

        wl, _ = Watchlist.objects.get_or_create(
            user=user, is_default=True, defaults={"name": "My Watchlist"}
        )
        for sym in WATCHLIST_TICKERS:
            WatchlistTicker.objects.get_or_create(watchlist=wl, ticker=sym)
        self.stdout.write(f"watchlist '{wl.name}' has {wl.tickers.count()} tickers")
