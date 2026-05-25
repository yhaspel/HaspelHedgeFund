"""``python manage.py evolve_personas [--persona X] [--force] [--user EMAIL]``.

Wraps ``apps.persona_evolution.tasks.evolve_personas`` so an operator can
trigger a cycle from the shell without going through Celery.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.persona_evolution.tasks import evolve_personas


class Command(BaseCommand):
    help = "Run the persona-evolution cycle for one or all personas."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--persona", default=None, help="Single persona slug.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass cadence gating (still honours is_evolvable and the cost cap).",
        )
        parser.add_argument(
            "--user",
            default=None,
            help="Email of the user whose settings to use (default: the enabled user).",
        )

    def handle(self, *args, **opts) -> None:
        user_id: int | None = None
        if opts.get("user"):
            User = get_user_model()
            user = User.objects.filter(email=opts["user"]).first()
            if user is None:
                self.stderr.write(self.style.ERROR(f"User not found: {opts['user']}"))
                return
            user_id = user.id
        result = evolve_personas(
            user_id=user_id,
            persona=opts.get("persona"),
            force=opts.get("force", False),
        )
        self.stdout.write(self.style.SUCCESS(repr(result)))
