"""Nightly leaderboard recompute task (P3b)."""
from __future__ import annotations

from celery import shared_task

from .compute import recompute_all
from .news_decisions import score_news_decisions


@shared_task(name="apps.leaderboard.tasks.recompute_leaderboards")
def recompute_leaderboards() -> dict:
    out = recompute_all()
    # P10 §E4: grade the news-lab's name-level decisions once their forward
    # window has elapsed (idempotent; piggybacks the nightly recompute).
    out["news_decisions"] = score_news_decisions()
    return out
