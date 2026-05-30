"""Nightly leaderboard recompute task (P3b)."""
from __future__ import annotations

from celery import shared_task

from .compute import recompute_all


@shared_task(name="apps.leaderboard.tasks.recompute_leaderboards")
def recompute_leaderboards() -> dict:
    return recompute_all()
