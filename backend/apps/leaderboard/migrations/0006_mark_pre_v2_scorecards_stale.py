"""Data migration: nothing computed by the pre-wave-3 maths may be presented
as a real ratio before ``recompute_leaderboard`` has run.

Chosen mechanism: ``metrics_version`` (added in 0005), NOT ``provisional=True``.
It is the less invasive of the two the brief offered:

* ``provisional`` already means one specific thing to the API and the UI —
  "the sample is too small" — and the UI greys those rows out. Overloading it
  with "stale maths" would mislabel a 500-cycle strategy as low-sample and
  would be unrecoverable (the recompute cannot tell the two apart afterwards).
* ``metrics_version`` is additive, self-describing, and lets the API/UI say
  "awaiting recompute" separately from "small sample". It also gives ops a
  gate: ``manage.py recompute_leaderboard --check``.

Belt and braces: because a stale row is still readable until the recompute
runs, this migration also NULLs the three columns that were wrong by
construction on those rows — ``sharpe``, ``sortino`` and
``annualised_return_pct`` (chained overlapping windows + a hard-coded 252).
``n_cycles`` / ``total_return_pct`` / ``hit_rate`` / ``max_drawdown_pct`` are
left alone.

Legacy per-flavor aggregate rows (``strategy IS NULL``) had no owner column at
all and were served to every authenticated user, so they cannot be backfilled
to a tenant — they are deleted. Per-strategy rows get their owner from the
strategy they belong to; agent rows get theirs from... nothing (they were
global), so they are deleted too and rebuilt per-user by the recompute.
"""
from __future__ import annotations

from django.db import migrations

METRICS_VERSION = 2


def mark_stale(apps, schema_editor):
    StrategyScorecard = apps.get_model("leaderboard", "StrategyScorecard")
    AgentScorecard = apps.get_model("leaderboard", "AgentScorecard")

    # Cross-tenant aggregates and global agent rows cannot be attributed.
    StrategyScorecard.objects.filter(strategy__isnull=True).delete()
    AgentScorecard.objects.filter(user__isnull=True).delete()

    # Per-strategy rows: owner is unambiguous; keep them but strip the ratios
    # the old maths got wrong until the recompute rewrites them.
    for row in StrategyScorecard.objects.filter(
        strategy__isnull=False
    ).select_related("strategy").iterator():
        row.user_id = row.strategy.user_id
        row.sharpe = None
        row.sortino = None
        row.annualised_return_pct = None
        row.sortino_note = "stale: awaiting recompute under metrics_version 2"
        row.n_observations = 0
        row.metrics_version = 0
        row.save(update_fields=[
            "user", "sharpe", "sortino", "annualised_return_pct",
            "sortino_note", "n_observations", "metrics_version",
        ])


def noop(apps, schema_editor):
    """Irreversible by design — the pre-fix numbers are not worth restoring."""


class Migration(migrations.Migration):

    dependencies = [
        ("leaderboard", "0005_scorecard_owner_and_metrics_version"),
    ]

    operations = [
        migrations.RunPython(mark_stale, noop),
    ]
