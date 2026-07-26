"""P13: heal every stored per-agent model map against the live catalog.

Usage:
    python manage.py model_map_doctor            # heal and report
    python manage.py model_map_doctor --dry-run  # report only, write nothing

Covers ScheduledRun.model_overrides, UserModelPreferences per-agent/per-tier
defaults, AgentGraphVersion node models + tail_models, and still-QUEUED Run
rows. The same healer runs automatically inside the daily
``reconcile_model_catalog`` task; this command exists for on-demand repair
(e.g. right after a deploy that shipped new healing logic).

Exit code 0 always — healing is maintenance, not a gate. The report lists
every substitution as ``store#id agent: dead-id -> live-id``.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Heal stored per-agent model maps that reference dead catalog models."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be healed without writing anything.",
        )

    def handle(self, *args, **options) -> None:
        from apps.models_catalog.doctor import heal_stored_model_maps

        if options["dry_run"]:
            with transaction.atomic():
                report = heal_stored_model_maps()
                transaction.set_rollback(True)
            self.stdout.write("DRY RUN — nothing was written.")
        else:
            report = heal_stored_model_maps()

        if not report:
            self.stdout.write(self.style.SUCCESS(
                "model_map_doctor: all stored model maps are live — nothing to heal."
            ))
            return
        total = sum(len(r["moves"]) for r in report)
        for r in report:
            for m in r["moves"]:
                self.stdout.write(
                    f"{r['store']}#{r['id']}  {m['agent']}: {m['from']} -> {m['to']}"
                )
        self.stdout.write(self.style.SUCCESS(
            f"model_map_doctor: healed {total} dead model reference(s) "
            f"across {len(report)} row(s)."
        ))
