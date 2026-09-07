"""WP B1 — tests for fixes with no proof test of their own.

Companion to ``tests/test_review_secconf_findings.py``: that file proves the
findings are gone, this one pins the behaviour the fixes must NOT break (an
Ollama host on the LAN still works, a legitimate cron is still accepted) plus
the new surfaces the fixes introduced (logout, the catch-up note, the encrypted
channel secret).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import logging.config
from decimal import Decimal

import httpx
import pytest
import respx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.models_catalog import ollama_discovery
from apps.notifications.models import NotificationChannel
from apps.schedules import tasks as sched_tasks
from apps.schedules.models import ScheduledRun, ScheduledRunHistory
from apps.watchlists.models import Watchlist, WatchlistTicker

User = get_user_model()
PW = "correct-horse-battery-staple-9"


@pytest.fixture
def user(db):
    return User.objects.create_user(email="b1-owner@x.test", password=PW)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture(autouse=True)
def _isolate_caches():
    ollama_discovery._CACHE.clear()
    cache.clear()
    yield
    ollama_discovery._CACHE.clear()
    cache.clear()


def _watchlist(user, tickers=("AAPL",)):
    wl = Watchlist.objects.create(user=user, name="W", is_default=True)
    for t in tickers:
        WatchlistTicker.objects.create(watchlist=wl, ticker=t)
    return wl


# ---------------------------------------------------------------- SSRF guard


@pytest.mark.django_db
@pytest.mark.parametrize(
    "host",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://192.168.1.20:11434",   # RFC1918 — Ollama on the LAN
        "http://10.0.0.7:11434",
        "http://172.16.4.4:11434",
        "https://ollama.home.lan:11434",
        "http://[::1]:11434",
        "",                            # unset
    ],
)
def test_legitimate_ollama_hosts_are_still_accepted(client, host):
    """The SSRF guard must not break the normal case: Ollama runs on loopback
    or the LAN, and both stay allowed."""
    resp = client.put("/api/me/provider-keys/", {"ollama_host": host}, format="json")
    assert resp.status_code == 200, resp.json()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "host",
    [
        "http://169.254.169.254/",          # AWS/GCP metadata
        "http://[fe80::1]:11434",           # IPv6 link-local
        "http://user:pw@ollama.example.com",  # embedded credentials
        "http://ollama.example.com:99999",  # port out of range
        "ftp://ollama.example.com",
        "//evil.example.com/api/tags",      # scheme-relative
    ],
)
def test_unsafe_ollama_hosts_are_rejected(client, host):
    resp = client.put("/api/me/provider-keys/", {"ollama_host": host}, format="json")
    assert resp.status_code == 400, f"{host!r} was accepted"


def test_discovery_never_raises_on_a_malformed_host():
    """`/api/models/`, `/api/presets/<n>/` and `/api/health/` all call this; it
    must degrade to 'no local models', never propagate httpx.InvalidURL."""
    assert ollama_discovery.discover_ollama_models("http://[::1") == []
    assert ollama_discovery.discover_ollama_models("gopher://internal") == []
    assert ollama_discovery.discover_ollama_models("") == []


# ------------------------------------------------------- missed-fire catch-up


@pytest.mark.django_db
def test_catch_up_records_how_many_slots_were_skipped(user, monkeypatch):
    wl = _watchlist(user)
    sr = ScheduledRun.objects.create(
        user=user, name="hourly", watchlist=wl, cron_expression="0 * * * *",
        timezone="UTC", is_market_aware=False,
    )
    sr.next_run_at = (timezone.now() - dt.timedelta(hours=5)).replace(
        minute=0, second=0, microsecond=0
    )
    sr.save(update_fields=["next_run_at"])
    monkeypatch.setattr(sched_tasks.execute_scheduled_run, "delay", lambda *a, **k: None)

    sched_tasks.dispatch_due_scheduled_runs()

    sr.refresh_from_db()
    assert sr.next_run_at > timezone.now(), "next_run_at must land in the FUTURE"
    hist = ScheduledRunHistory.objects.get(scheduled_run=sr)
    assert "catch-up:" in hist.error and "slots skipped" in hist.error, hist.error


@pytest.mark.django_db
def test_a_normal_on_time_fire_carries_no_catch_up_note(user, monkeypatch):
    wl = _watchlist(user)
    sr = ScheduledRun.objects.create(
        user=user, name="hourly", watchlist=wl, cron_expression="0 * * * *",
        timezone="UTC", is_market_aware=False,
    )
    sr.next_run_at = timezone.now() - dt.timedelta(seconds=5)
    sr.save(update_fields=["next_run_at"])
    monkeypatch.setattr(sched_tasks.execute_scheduled_run, "delay", lambda *a, **k: None)

    sched_tasks.dispatch_due_scheduled_runs()

    assert ScheduledRunHistory.objects.get(scheduled_run=sr).error == ""


# ----------------------------------------------------------- cron validation


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expr", ["25 9 * * 1-5", "0 * * * *", "*/15 * * * *", "30 16 * * 5", "0 0 1 * *"]
)
def test_reasonable_cron_expressions_are_still_accepted(client, user, expr):
    wl = _watchlist(user)
    resp = client.post(
        "/api/scheduled-runs/",
        {"name": f"ok-{expr}", "watchlist": wl.id, "cron_expression": expr,
         "is_market_aware": False},
        format="json",
    )
    assert resp.status_code == 201, resp.json()


@pytest.mark.django_db
def test_unknown_timezone_is_rejected(client, user):
    wl = _watchlist(user)
    resp = client.post(
        "/api/scheduled-runs/",
        {"name": "tz", "watchlist": wl.id, "cron_expression": "0 9 * * 1-5",
         "timezone": "Mars/Olympus_Mons", "is_market_aware": False},
        format="json",
    )
    assert resp.status_code == 400
    assert "timezone" in resp.json()


@pytest.mark.django_db
def test_every_ten_minutes_is_rejected_but_every_fifteen_is_not(client, user):
    wl = _watchlist(user)

    def _post(expr):
        return client.post(
            "/api/scheduled-runs/",
            {"name": expr, "watchlist": wl.id, "cron_expression": expr,
             "is_market_aware": False},
            format="json",
        ).status_code

    assert _post("*/10 * * * *") == 400
    assert _post("*/15 * * * *") == 201


# ------------------------------------------------------------ child budgets


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("ceiling", "n", "expected"),
    [
        (Decimal("1.00"), 2, Decimal("0.50")),
        (Decimal("0.30"), 5, Decimal("0.06")),
        (Decimal("0.10"), 20, Decimal("0.05")),  # floor
        (None, 3, None),
    ],
)
def test_per_run_budget_is_the_ceiling_share_with_a_floor(ceiling, n, expected):
    assert sched_tasks._per_run_budget(ceiling, n) == expected


# -------------------------------------------------------------- auth surface


@pytest.mark.django_db
def test_logout_blacklists_the_refresh_token(user):
    c = APIClient()
    tokens = c.post("/api/auth/login/", {"email": user.email, "password": PW},
                    format="json").json()
    out = c.post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
    assert out.status_code == 205
    replay = c.post("/api/auth/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401
    # Idempotent: logging out twice is not an error.
    assert c.post("/api/auth/logout/", {"refresh": tokens["refresh"]},
                  format="json").status_code == 205


@pytest.mark.django_db
def test_logout_without_a_token_is_a_400(user):
    assert APIClient().post("/api/auth/logout/", {}, format="json").status_code == 400


@pytest.mark.django_db
def test_a_strong_password_is_still_accepted_at_signup():
    resp = APIClient().post(
        "/api/auth/signup/",
        {"email": "fresh@x.test", "password": "quiet-otter-marble-77"},
        format="json",
    )
    assert resp.status_code == 201, resp.json()


@pytest.mark.django_db
def test_only_the_credential_endpoints_are_throttled(client, user):
    """A throttle on the analysis endpoints would break normal use — the scoped
    throttle must apply to /auth/ and the test-send only."""
    _watchlist(user)
    statuses = {client.get("/api/watchlists/").status_code for _ in range(25)}
    assert 429 not in statuses, statuses


# ------------------------------------------------------------- notifications


@pytest.mark.django_db
def test_email_test_send_is_redirected_to_the_callers_own_address(client, user):
    from django.core import mail

    resp = client.post(
        "/api/notification-channels/",
        {"kind": "email", "name": "x", "config": {"address": "stranger@example.com"}},
        format="json",
    )
    ch_id = resp.json()["id"]
    out = client.post(f"/api/notification-channels/{ch_id}/test/")
    assert out.status_code == 200
    assert out.json()["sent_to"] == user.email
    assert [m.to for m in mail.outbox] == [[user.email]]


@pytest.mark.django_db
def test_channel_bot_token_round_trips_through_encryption(user):
    from apps.notifications.services import send_notification

    token = "424242:ROUND-TRIP-TOKEN"
    ch = NotificationChannel.objects.create(
        user=user, kind="telegram", config={"bot_token": token, "chat_id": "7"}
    )
    stored = NotificationChannel.objects.get(pk=ch.pk).config
    assert "bot_token" not in stored and stored["bot_token_enc"]
    assert stored["bot_token_enc"] != token
    # …and the sender still authenticates with the real token.
    ch = NotificationChannel.objects.get(pk=ch.pk)
    assert ch.get_secret("bot_token") == token
    with respx.mock(base_url=settings.TELEGRAM_API_BASE) as m:
        route = m.post(f"/bot{token}/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        ev = send_notification(ch, "s", "b", enforce_cap=False)
    assert route.called
    assert ev.delivery_status == "sent"


@pytest.mark.django_db
def test_channel_read_exposes_has_token_but_never_the_token(client):
    resp = client.post(
        "/api/notification-channels/",
        {"kind": "telegram", "config": {"bot_token": "9:VERY-SECRET", "chat_id": "1"}},
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_token"] is True
    assert "VERY-SECRET" not in json.dumps(body)


# ------------------------------------------------------------------ logging


@pytest.fixture
def root_log_buffer():
    cfg = json.loads(json.dumps(settings.LOGGING))
    logging.config.dictConfig(cfg)
    handler = logging.getLogger().handlers[0]
    old_stream, buf = handler.stream, io.StringIO()
    handler.stream = buf
    try:
        yield buf
    finally:
        handler.stream = old_stream


@pytest.mark.parametrize(
    "line",
    [
        "GET https://api.example.com/v1/x?apikey=SUPERSECRET&sym=AAPL",
        "provider call failed: apikey=SUPERSECRET",
        "token=SUPERSECRET",
        "https://api.telegram.org/bot123456:SUPERSECRET/sendMessage",
    ],
)
def test_redact_filter_scrubs_bare_and_path_credentials(root_log_buffer, line):
    logging.getLogger("apps.data.providers").warning("outbound %s", line)
    out = root_log_buffer.getvalue()
    assert "SUPERSECRET" not in out, out


def test_httpx_request_logging_is_off_by_default():
    assert settings.LOGGING["loggers"]["httpx"]["level"] == "WARNING"
    assert settings.LOGGING["loggers"]["httpcore"]["level"] == "WARNING"
