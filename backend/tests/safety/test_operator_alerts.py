"""P5-SH WS2.2 — operator alerts via the existing notifications app.

Acceptance: with zero third-party vendors configured, the operator receives an
email/Telegram alert for a failed run (owner), an orphan sweep (superusers), and
a repeated provider outage (superusers, throttled). Delivery here goes through
Django's locmem email backend — no external service.
"""
from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from apps.notifications import operator as op
from apps.notifications.models import NotificationChannel, NotificationEvent

pytestmark = pytest.mark.django_db


def _user_with_channel(email, *, superuser=False):
    u = get_user_model().objects.create_user(
        email=email, password="x", is_superuser=superuser,
    )
    NotificationChannel.objects.create(
        user=u, kind=NotificationChannel.EMAIL,
        config={"address": email}, is_active=True,
    )
    return u


def test_run_failed_alerts_the_owner():
    from apps.runs.models import Run

    owner = _user_with_channel("owner@example.test")
    run = Run.objects.create(
        user=owner, tickers=["AAPL"], as_of_date=dt.date(2026, 6, 1),
        status=Run.FAILED, error_message="budget_exceeded: spent $0.10 >= cap $0.05",
    )
    assert op.notify_run_failed(run) == 1
    ev = NotificationEvent.objects.filter(channel__user=owner).latest("created_at")
    assert ev.delivery_status == NotificationEvent.SENT
    assert "failed" in ev.subject.lower()
    assert "budget_exceeded" in ev.body


def test_orphan_sweep_alerts_the_operator():
    op_user = _user_with_channel("op@example.test", superuser=True)
    assert op.notify_orphan_sweep(3, run_ids=[1, 2, 3]) == 1
    ev = NotificationEvent.objects.filter(channel__user=op_user).latest("created_at")
    assert ev.delivery_status == NotificationEvent.SENT
    assert "orphan" in ev.subject.lower()


def test_orphan_sweep_zero_is_a_noop():
    _user_with_channel("op@example.test", superuser=True)
    assert op.notify_orphan_sweep(0) == 0
    assert not NotificationEvent.objects.exists()


def test_provider_outage_is_throttled(settings):
    settings.OFFLINE_MODE = False
    cache.clear()
    op_user = _user_with_channel("op@example.test", superuser=True)
    # First two strikes stay under the threshold → no alert.
    assert op.record_provider_failure("fmp") is False
    assert op.record_provider_failure("fmp") is False
    # The third crosses the threshold → exactly one alert.
    assert op.record_provider_failure("fmp") is True
    # A fourth within the cooldown must not re-alert.
    assert op.record_provider_failure("fmp") is False
    events = NotificationEvent.objects.filter(
        channel__user=op_user, subject__icontains="outage"
    )
    assert events.count() == 1
    assert events.first().delivery_status == NotificationEvent.SENT


def test_provider_failure_is_noop_in_offline_mode(settings):
    settings.OFFLINE_MODE = True
    cache.clear()
    _user_with_channel("op@example.test", superuser=True)
    for _ in range(5):
        assert op.record_provider_failure("fmp") is False
    assert not NotificationEvent.objects.exists()


def test_only_superusers_receive_system_alerts():
    # A non-superuser with a channel must NOT receive orphan-sweep alerts.
    _user_with_channel("plain@example.test", superuser=False)
    assert op.notify_orphan_sweep(2, run_ids=[9]) == 0
    assert not NotificationEvent.objects.exists()
