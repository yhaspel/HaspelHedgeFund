"""Refresh dev/frugal ModelEntry rows from the live OpenRouter catalog.

Usage:
    uv run python manage.py fetch_openrouter_models
    uv run python manage.py fetch_openrouter_models --dry-run
    uv run python manage.py fetch_openrouter_models --json

Exit code is non-zero when anything was deactivated or excluded — so the
command is usable as a loud manual check (a `:free` slug going paid, a
slug renamed upstream, etc.).
"""
from __future__ import annotations

import json as _json

from django.core.management.base import BaseCommand, CommandError

from apps.models_catalog.fetching import sync_tier_models


class Command(BaseCommand):
    help = (
        "Fetch the live OpenRouter catalog and refresh dev/frugal ModelEntry "
        "rows. Allowlisted slugs in tier_menus.py are the curation surface; "
        "pricing/context/display name come from the live response."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute the sync without writing to the DB.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit JSON report.",
        )

    def handle(self, *args, **opts):
        try:
            result = sync_tier_models(dry_run=opts["dry_run"])
        except Exception as e:
            raise CommandError(f"fetch failed: {e}") from e

        if opts["json"]:
            self.stdout.write(_json.dumps(result.as_dict(), indent=2))
        else:
            for mid in result.synced:
                marker = "+" if mid in result.created else "✓"
                self.stdout.write(f"  {marker} {mid}")
            for mid in result.deactivated:
                self.stdout.write(self.style.WARNING(f"  ✗ {mid} (missing upstream)"))
            for entry in result.excluded:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ! openrouter:{entry['slug']} excluded — {entry['reason']}"
                    )
                )
            for mid in result.swept:
                self.stdout.write(f"  ⌫ {mid} (retired — missing upstream)")
            self.stdout.write("")
            summary = (
                f"synced={len(result.synced)} "
                f"created={len(result.created)} "
                f"deactivated={len(result.deactivated)} "
                f"excluded={len(result.excluded)} "
                f"swept={len(result.swept)} "
                f"dry_run={opts['dry_run']}"
            )
            failed = bool(result.deactivated or result.excluded)
            self.stdout.write(
                self.style.ERROR(summary) if failed else self.style.SUCCESS(summary)
            )

        if result.deactivated or result.excluded:
            raise CommandError(
                f"{len(result.deactivated)} deactivated, "
                f"{len(result.excluded)} excluded — re-curate the allowlist."
            )
