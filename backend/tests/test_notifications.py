"""Notification channels, materiality gating, and delivery (P3b)."""
from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from rest_framework.test import APIClient

from apps.models_catalog.presets import PERSONA_AGENTS
from apps.notifications import materiality
from apps.notifications.models import NotificationChannel, NotificationEvent
from apps.notifications.services import send_notification
from apps.runs.models import AgentMessage, Decision, Run

User = get_user_model()
PERSONA_LIST = sorted(PERSONA_AGENTS)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="notif@x.test", password="pw-fake-123456789")


@pytest.fixture
def auth_client(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def make_run(user, ticker, signals, *, confidence=70, veto=False, news=None,
             status=Run.DONE):
    run = Run.objects.create(
        user=user, tickers=[ticker], as_of_date=dt.date(2026, 5, 29), status=status
    )
    for i, sig in enumerate(signals):
        AgentMessage.objects.create(
            run=run, agent_name=PERSONA_LIST[i],
            parsed_output={"signal": sig, "confidence": confidence},
        )
    Decision.objects.create(
        run=run, ticker=ticker, action="buy", confidence=confidence,
        risk_overrides={"veto": veto},
    )
    if news:
        AgentMessage.objects.create(
            run=run, agent_name="news_digest",
            parsed_output={"material_events": news},
        )
    return run


# ---------- materiality ----------


def test_unanimous_bullish_new_ticker_notifies(user):
    cur = make_run(user, "AAPL", ["bullish"] * 5)
    res = materiality.evaluate(cur, None)
    assert res["notify"] is True
    assert any("unanimous" in r for r in res["reasons"])


def test_signal_flip_notifies(user):
    prior = make_run(user, "AAPL", ["bearish"] * 5, confidence=60)
    cur = make_run(user, "AAPL", ["bullish"] * 5, confidence=60)
    res = materiality.evaluate(cur, prior)
    assert res["notify"] is True
    assert any("signal flip" in r for r in res["reasons"])


def test_confidence_delta_notifies(user):
    prior = make_run(user, "AAPL", ["bullish", "neutral", "bearish"], confidence=40)
    cur = make_run(user, "AAPL", ["bullish", "neutral", "bearish"], confidence=70)
    res = materiality.evaluate(cur, prior)
    assert res["notify"] is True
    assert any("confidence" in r for r in res["reasons"])


def test_no_change_does_not_notify(user):
    prior = make_run(user, "AAPL", ["bullish", "neutral", "bearish"], confidence=55)
    cur = make_run(user, "AAPL", ["bullish", "neutral", "bearish"], confidence=55)
    res = materiality.evaluate(cur, prior)
    assert res["notify"] is False
    assert res["reasons"] == []


def test_first_veto_notifies(user):
    prior = make_run(user, "AAPL", ["bullish"] * 3, confidence=55, veto=False)
    cur = make_run(user, "AAPL", ["bullish"] * 3, confidence=55, veto=True)
    res = materiality.evaluate(cur, prior)
    assert res["notify"] is True
    assert any("veto" in r for r in res["reasons"])


def test_new_material_news_notifies(user):
    prior = make_run(user, "AAPL", ["neutral"] * 3, confidence=55)
    cur = make_run(
        user, "AAPL", ["neutral"] * 3, confidence=55,
        news=[{"headline": "FDA approval", "date": "2026-05-29", "materiality": 9}],
    )
    res = materiality.evaluate(cur, prior)
    assert res["notify"] is True
    assert any("news" in r for r in res["reasons"])


# ---------- channel CRUD + redaction ----------


def test_channel_create_and_token_redacted(auth_client):
    r = auth_client.post(
        "/api/notification-channels/",
        {"kind": "telegram", "name": "My bot",
         "config": {"bot_token": "12345:SECRETTOKEN", "chat_id": "999"}},
        format="json",
    )
    assert r.status_code == 201, r.json()
    body = r.json()
    assert "config" not in body  # write-only
    assert body["config_summary"]["chat_id"] == "999"
    assert "SECRET" not in str(body)  # token never leaks
    assert body["config_summary"]["bot_token"].endswith("OKEN")


def test_telegram_channel_requires_token_and_chat(auth_client):
    r = auth_client.post(
        "/api/notification-channels/",
        {"kind": "telegram", "config": {"bot_token": "x"}},
        format="json",
    )
    assert r.status_code == 400


# ---------- delivery ----------


def test_email_test_endpoint_sends(auth_client, user):
    ch = NotificationChannel.objects.create(
        user=user, kind="email", config={"address": "me@x.test"}
    )
    r = auth_client.post(f"/api/notification-channels/{ch.id}/test/")
    assert r.status_code == 200
    assert r.json()["delivery_status"] == "sent"
    assert len(mail.outbox) == 1
    assert "Test notification" in mail.outbox[0].subject


@respx.mock
def test_telegram_test_endpoint_sends(auth_client, user):
    ch = NotificationChannel.objects.create(
        user=user, kind="telegram", config={"bot_token": "abc", "chat_id": "42"}
    )
    route = respx.post("https://api.telegram.test/botabc/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    r = auth_client.post(f"/api/notification-channels/{ch.id}/test/")
    assert r.status_code == 200
    assert r.json()["delivery_status"] == "sent"
    assert route.called


@respx.mock
def test_telegram_failure_recorded(auth_client, user):
    ch = NotificationChannel.objects.create(
        user=user, kind="telegram", config={"bot_token": "abc", "chat_id": "42"}
    )
    respx.post("https://api.telegram.test/botabc/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "bad chat"})
    )
    r = auth_client.post(f"/api/notification-channels/{ch.id}/test/")
    assert r.status_code == 502
    assert r.json()["delivery_status"] == "failed"


@override_settings(NOTIFICATIONS_MAX_PER_DAY=2)
def test_daily_cap_throttles(user):
    ch = NotificationChannel.objects.create(
        user=user, kind="email", config={"address": "me@x.test"}
    )
    send_notification(ch, "s1", "b")
    send_notification(ch, "s2", "b")
    ev = send_notification(ch, "s3", "b")
    assert ev.delivery_status == NotificationEvent.THROTTLED
    # test endpoint bypasses the cap
    ev2 = send_notification(ch, "test", "b", enforce_cap=False)
    assert ev2.delivery_status == NotificationEvent.SENT
