"""Verify every OpenRouter ModelEntry against the live OpenRouter catalog.

Usage:
    uv run python manage.py verify_openrouter_pricing
    uv run python manage.py verify_openrouter_pricing --json
    uv run python manage.py verify_openrouter_pricing --model openrouter:openai/gpt-oss-120b:free

Exit code is non-zero when any row drifted or went missing — suitable for
cron / CI as a loud signal that a "free" model is no longer free, or that
a slug was renamed/removed upstream.
"""
from __future__ import annotations

import json as _json

from django.core.management.base import BaseCommand, CommandError

from apps.models_catalog.verification import verify_models


class Command(BaseCommand):
    help = "Verify OpenRouter ModelEntry pricing against the live /api/v1/models catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            action="append",
            dest="models",
            help="Limit to one or more ModelEntry ids (repeatable). Default: all openrouter:* rows.",
        )
        parser.add_argument("--json", action="store_true", help="Emit JSON report.")

    def handle(self, *args, **opts):
        results = verify_models(model_ids=opts.get("models") or None)
        if not results:
            self.stdout.write("No matching OpenRouter ModelEntry rows.")
            return
        bad = [r for r in results if not r.ok]
        if opts["json"]:
            self.stdout.write(_json.dumps({
                "checked": len(results),
                "ok": len(results) - len(bad),
                "failed": len(bad),
                "results": [r.as_dict() for r in results],
            }, indent=2))
        else:
            for r in results:
                mark = "✓" if r.ok else "✗"
                detail = r.note or (
                    f"db ${r.db_price_in}/${r.db_price_out} == "
                    f"upstream ${r.upstream_price_in}/${r.upstream_price_out} per Mtok"
                )
                self.stdout.write(f"  {mark} {r.model_id}: {detail}")
            self.stdout.write("")
            summary = f"checked={len(results)} ok={len(results) - len(bad)} failed={len(bad)}"
            self.stdout.write(
                self.style.SUCCESS(summary) if not bad else self.style.ERROR(summary)
            )
        if bad:
            raise CommandError(
                f"{len(bad)} OpenRouter model(s) failed pricing verification."
            )
