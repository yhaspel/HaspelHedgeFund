"""Investor profile data models (P3-prereq-5 WS-A).

Two per-user concepts:

* ``QuestionnaireResponse`` — one row per submission *or* tune. The set is the
  user's profile **history**: every retake and every fine-tune is kept. The
  *active* profile is the most recent ``status="done"`` row (questionnaire or
  tuned).
* ``InvestorProfileState`` — one row per user. Holds the monthly-nudge
  bookkeeping and the master "apply my profile to runs" switch.

Nothing in this app is allowed inside the backtest path. The PIT regression
test in ``apps/backtests/tests/test_pit_imports.py`` asserts the boundary.
"""
from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone


class QuestionnaireResponseManager(models.Manager):
    def active_for(self, user):
        """Return the latest *done* response (or None) for the user."""
        return (
            self.get_queryset()
            .filter(user=user, analysis_status=QuestionnaireResponse.DONE)
            .order_by("-created_at")
            .first()
        )


class QuestionnaireResponse(models.Model):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (DONE, "Done"),
        (FAILED, "Failed"),
    ]

    SOURCE_QUESTIONNAIRE = "questionnaire"
    SOURCE_TUNED = "tuned"
    SOURCE_CHOICES = [
        (SOURCE_QUESTIONNAIRE, "Questionnaire"),
        (SOURCE_TUNED, "Tuned"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="questionnaire_responses",
        on_delete=models.CASCADE,
    )
    source = models.CharField(
        max_length=14, choices=SOURCE_CHOICES, default=SOURCE_QUESTIONNAIRE
    )
    derived_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tunes",
    )
    schema_version = models.PositiveSmallIntegerField()
    answers = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    model_id = models.CharField(max_length=128)
    analysis_status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=PENDING
    )
    analyzed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    analysis = models.JSONField(default=dict, blank=True)
    profile_summary = models.TextField(blank=True, default="")
    agent_brief = models.TextField(blank=True, default="")

    objects = QuestionnaireResponseManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "analysis_status"]),
        ]

    def __str__(self) -> str:
        return f"QR(user={self.user_id}, source={self.source}, status={self.analysis_status})"


class InvestorProfileState(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="investor_profile_state",
        on_delete=models.CASCADE,
    )
    nudge_dismiss_count = models.PositiveSmallIntegerField(default=0)
    nudge_last_dismissed_at = models.DateTimeField(null=True, blank=True)
    apply_to_runs = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"State(user={self.user_id}, apply={self.apply_to_runs})"


NUDGE_INTERVAL_DAYS = 30


def compute_nudge(
    state: InvestorProfileState | None,
    has_done_questionnaire: bool,
    now: dt.datetime | None = None,
) -> dict:
    """Return {due, form} for the welcome-modal / monthly-banner nudge.

    Rules (plan §5):
        - completed questionnaire → never due.
        - no state row → due, form="modal" (first run).
        - last dismissal < 30 days ago → not due (silent grace).
        - otherwise → due. form is "modal" the first time (dismiss_count==0)
          and "banner" thereafter.
    """
    if has_done_questionnaire:
        return {"due": False, "form": None}
    if state is None:
        return {"due": True, "form": "modal"}
    now = now or timezone.now()
    last = state.nudge_last_dismissed_at
    if last is not None and (now - last) < dt.timedelta(days=NUDGE_INTERVAL_DAYS):
        return {"due": False, "form": None}
    form = "modal" if state.nudge_dismiss_count == 0 else "banner"
    return {"due": True, "form": form}
