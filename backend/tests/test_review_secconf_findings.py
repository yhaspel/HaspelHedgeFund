"""Adversarial review (reviewer: secconf) — proof tests for security/config findings.

Every test here asserts the *expected secure/correct* behaviour, so each one
FAILS on HEAD 07d45e7 and documents one finding (F-numbers match the review
report). They are deliberately not xfail-marked: a failure is the proof.

Run:
  DJANGO_SETTINGS_MODULE=hedgefund.settings.test /tmp/v312/bin/pytest \
      tests/test_review_secconf_findings.py -q -p no:cacheprovider
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
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.models_catalog import ollama_discovery
from apps.models_catalog.models import ModelEntry
from apps.notifications.models import NotificationChannel
from apps.runs.models import Run
from apps.schedules import tasks as sched_tasks
from apps.schedules.models import ScheduledRun, ScheduledRunHistory
from apps.watchlists.models import Watchlist, WatchlistTicker

User = get_user_model()
PW = "correct-horse-battery-staple-9"


@pytest.fixture
def user(db):
    return User.objects.create_user(email="victim-owner@x.test", password=PW)


@pytest.fixture
def attacker(db):
    """A valid, low-privilege (non-staff) account — the threat model in the brief."""
    return User.objects.create_user(email="lowpriv@x.test", password=PW)


@pytest.fixture
def client(attacker):
    c = APIClient()
    c.force_authenticate(attacker)
    return c


@pytest.fixture(autouse=True)
def _clear_ollama_cache():
    ollama_discovery._CACHE.clear()
    yield
    ollama_discovery._CACHE.clear()


@pytest.fixture(autouse=True)
def _clear_throttle_state():
    """DRF rate throttles keep their history in the shared cache, so a test that
    deliberately exhausts a scope (F3, F6) would otherwise 429 unrelated tests
    for the rest of the minute. Isolate each test."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _watchlist(user, tickers=("AAPL",)):
    wl = Watchlist.objects.create(user=user, name="W", is_default=True)
    for t in tickers:
        WatchlistTicker.objects.create(watchlist=wl, ticker=t)
    return wl


# =============================================================================
# F1 — SSRF: ProviderKey.ollama_host is an unvalidated free-form string that the
#      server fetches on every GET /api/models/ and /api/presets/<name>/.
# =============================================================================


@pytest.mark.django_db
def test_f1_ollama_host_rejects_non_http_and_link_local_targets(client):
    """The vault must not accept a cloud-metadata / internal target as an
    'Ollama host'. HEAD: every one of these is stored and returned verbatim (200)."""
    bad_hosts = [
        # AWS/GCP metadata (the query string swallows the appended /api/tags)
        "http://169.254.169.254/latest/meta-data/?x=",
        "http://redis:6379",                           # compose/Railway private service
        "file:///etc/passwd",
        "gopher://internal",
        "not a url",
    ]
    accepted = []
    for host in bad_hosts:
        resp = client.put("/api/me/provider-keys/", {"ollama_host": host}, format="json")
        if resp.status_code == 200:
            accepted.append(host)
    assert accepted == [], f"unsafe ollama_host values accepted verbatim: {accepted}"


@pytest.mark.django_db
def test_f1_server_fetches_user_controlled_url_on_models_list(client):
    """The SSRF was live, not theoretical: saving an internal URL with a trailing
    '?x=' (which neutralises the appended '/api/tags') made a plain
    GET /api/models/ issue a server-side GET to that internal URL, and reflected
    its JSON back as 'ollama:<name>'. Both halves must now be dead: the vault
    refuses the host, and nothing is fetched or reflected."""
    target = "http://169.254.169.254/latest/meta-data/iam/?x="
    resp = client.put("/api/me/provider-keys/", {"ollama_host": target}, format="json")
    assert resp.status_code == 400, "link-local metadata host must be rejected"

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__startswith="http://169.254.169.254/").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "exfil-oracle"}]})
        )
        resp = client.get("/api/models/")
    assert resp.status_code == 200
    # The server must not contact the attacker-chosen internal host ...
    assert not route.called, "expected NO outbound request to 169.254.169.254"
    # ... nor echo anything from it back to the caller (a read oracle).
    ids = [m["id"] for m in resp.json()["models"]]
    assert "ollama:exfil-oracle" not in ids, (
        "internal-host JSON content is reflected into /api/models/ as 'ollama:<name>'"
    )


@pytest.mark.django_db
def test_f1_discovery_refuses_an_unsafe_host_already_in_the_database(client, attacker):
    """Defence in depth: rows saved BEFORE the validator existed are still in the
    database, so the fetch path re-checks the host it is about to contact."""
    from apps.models_catalog.models import ProviderKey

    ProviderKey.objects.update_or_create(
        user=attacker, defaults={"ollama_host": "http://169.254.169.254/?x="}
    )
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__startswith="http://169.254.169.254/").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "exfil"}]})
        )
        resp = client.get("/api/models/")
    assert resp.status_code == 200
    assert not route.called, "a legacy unsafe ollama_host is still fetched"


@pytest.mark.django_db
def test_f1_scheduled_run_overrides_accept_unvalidated_ollama_ids(client, attacker):
    """Ad-hoc runs validate `ollama:` ids against live discovery
    (apps/models_catalog/overrides.py); scheduled runs do not, so an arbitrary
    ollama id + arbitrary host reaches the 900s-timeout POST path via /run-now/."""
    wl = _watchlist(attacker)
    resp = client.post(
        "/api/scheduled-runs/",
        {
            "name": "ssrf", "watchlist": wl.id, "cron_expression": "0 9 * * 1-5",
            "is_market_aware": False,
            "model_overrides": {"buffett": "ollama:does-not-exist-anywhere"},
        },
        format="json",
    )
    assert resp.status_code == 400, (
        f"schedule with unknown ollama: override accepted: {resp.status_code} "
        f"{resp.json().get('model_overrides')}"
    )


# =============================================================================
# F2 — Missed-fire catch-up storm: the dispatcher advances next_run_at from the
#      *fire time*, so every slot missed during beat downtime is replayed.
# =============================================================================


@pytest.mark.django_db
def test_f2_dispatcher_does_not_replay_every_missed_slot(user, monkeypatch):
    wl = _watchlist(user)
    sr = ScheduledRun.objects.create(
        user=user, name="hourly", watchlist=wl, cron_expression="0 * * * *",
        timezone="UTC", is_market_aware=False,
    )
    # Beat was down for 5 hours.
    sr.next_run_at = (timezone.now() - dt.timedelta(hours=5)).replace(
        minute=0, second=0, microsecond=0
    )
    sr.save(update_fields=["next_run_at"])

    calls: list = []
    monkeypatch.setattr(
        sched_tasks.execute_scheduled_run, "delay", lambda *a, **k: calls.append(a)
    )
    for _ in range(8):  # eight beat ticks after recovery
        sched_tasks.dispatch_due_scheduled_runs()

    fires = ScheduledRunHistory.objects.filter(scheduled_run=sr).count()
    assert len(calls) <= 1 and fires <= 1, (
        f"beat downtime of 5h replayed {len(calls)} stale fires "
        f"({fires} history rows) — each one is a full council run per ticker"
    )


# =============================================================================
# F3 — No throttling on login/signup, no password validators.
# =============================================================================


@pytest.mark.django_db
def test_f3_login_is_rate_limited(user):
    c = APIClient()
    statuses = []
    for i in range(40):
        r = c.post("/api/auth/login/", {"email": user.email, "password": f"wrong-{i}"},
                   format="json")
        statuses.append(r.status_code)
    assert 429 in statuses, f"40 failed logins never throttled: {set(statuses)}"


@pytest.mark.django_db
def test_f3_signup_rejects_trivial_passwords():
    c = APIClient()
    weak = ["12345678", "password", "aaaaaaaa"]
    accepted = [
        p for p in weak
        if c.post("/api/auth/signup/", {"email": f"{p}@x.test", "password": p},
                  format="json").status_code == 201
    ]
    assert accepted == [], f"trivial passwords accepted at signup: {accepted}"


def test_f3_password_validators_configured():
    assert settings.AUTH_PASSWORD_VALIDATORS, "AUTH_PASSWORD_VALIDATORS is empty"


# =============================================================================
# F4 — Refresh-token rotation without blacklist: a rotated (old) refresh token
#      keeps working for its full 7-day life; there is no revocation path.
# =============================================================================


@pytest.mark.django_db
def test_f4_rotated_refresh_token_is_revoked(user):
    c = APIClient()
    old_refresh = c.post("/api/auth/login/", {"email": user.email, "password": PW},
                         format="json").json()["refresh"]
    r1 = c.post("/api/auth/refresh/", {"refresh": old_refresh}, format="json")
    assert r1.status_code == 200 and r1.json()["refresh"] != old_refresh  # rotated
    replay = c.post("/api/auth/refresh/", {"refresh": old_refresh}, format="json")
    assert replay.status_code == 401, (
        f"old refresh token replayed successfully after rotation ({replay.status_code})"
    )


# =============================================================================
# F5 — Secrets reach the JSON logs: (a) Telegram bot token sits in the URL PATH
#      that httpx logs at INFO; (b) exc_info tracebacks are never scrubbed.
# =============================================================================


@pytest.fixture
def root_log_buffer():
    """Install the exact handler chain from settings.LOGGING (json formatter +
    context + redact filters) onto a buffer, and restore stderr afterwards."""
    cfg = json.loads(json.dumps(settings.LOGGING))
    logging.config.dictConfig(cfg)
    handler = logging.getLogger().handlers[0]
    old_stream, buf = handler.stream, io.StringIO()
    handler.stream = buf
    try:
        yield buf
    finally:
        handler.stream = old_stream


@pytest.mark.django_db
def test_f5a_telegram_bot_token_not_written_to_logs(user, root_log_buffer):
    from apps.notifications.services import send_notification

    token = "123456:ABC-SUPERSECRET-BOT-TOKEN"
    ch = NotificationChannel.objects.create(
        user=user, kind="telegram", config={"bot_token": token, "chat_id": "42"}
    )
    with respx.mock(base_url=settings.TELEGRAM_API_BASE) as m:
        m.post(path__regex=r"/bot.*/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        ev = send_notification(ch, "hi", "body", enforce_cap=False)
    assert ev.delivery_status == "sent"
    out = root_log_buffer.getvalue()
    assert token not in out, (
        "bot token logged in plaintext by the httpx INFO request line: "
        + next(line for line in out.splitlines() if token in line)[:200]
    )


def test_f5b_redact_filter_scrubs_exception_tracebacks(root_log_buffer):
    log = logging.getLogger("apps.schedules.autosubmit")
    req = httpx.Request(
        "GET", "https://financialmodelingprep.com/api/v3/quote/AAPL?apikey=PLATFORM_FMP_SECRET"
    )
    try:
        httpx.Response(429, request=req).raise_for_status()
    except httpx.HTTPStatusError:
        # Exactly what apps/schedules/autosubmit.py:51 does on a pricing failure.
        log.exception("auto-submit price lookup failed for %s", "AAPL")
    assert "PLATFORM_FMP_SECRET" not in root_log_buffer.getvalue(), (
        "RedactSecretsFilter scrubs msg/args only; the exc_info traceback carries "
        "the full provider URL including ?apikey=..."
    )


# =============================================================================
# F6 — Email test-send is an uncapped relay to an arbitrary address.
# =============================================================================


@pytest.mark.django_db
def test_f6_email_test_send_is_capped_or_owner_only(client, attacker):
    resp = client.post(
        "/api/notification-channels/",
        {"kind": "email", "name": "x", "config": {"address": "someone-else@example.com"}},
        format="json",
    )
    assert resp.status_code == 201
    ch_id = resp.json()["id"]
    cap = settings.NOTIFICATIONS_MAX_PER_DAY
    for _ in range(cap + 15):
        client.post(f"/api/notification-channels/{ch_id}/test/")
    delivered = [m for m in mail.outbox if "someone-else@example.com" in m.to]
    assert len(delivered) <= cap, (
        f"{len(delivered)} test emails delivered to a third-party address with no cap "
        f"(NOTIFICATIONS_MAX_PER_DAY={cap} is bypassed by enforce_cap=False)"
    )


# =============================================================================
# F7 — ScheduledRun.cost_ceiling_usd is a pre-flight *estimate* gate only; the
#      child Runs are created with max_budget_usd=None, so real spend is unbounded.
# =============================================================================


@pytest.mark.django_db
def test_f7_child_runs_inherit_a_hard_budget_from_the_schedule(user, monkeypatch):
    wl = _watchlist(user, ["AAPL", "MSFT"])
    sr = ScheduledRun.objects.create(
        user=user, name="capped", watchlist=wl, cron_expression="0 9 * * 1-5",
        is_market_aware=False, cost_ceiling_usd=Decimal("1.00"),
        on_breach=ScheduledRun.NOTIFY_ONLY,
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())

    def _fake_execute(run_id):
        run = Run.objects.get(pk=run_id)
        run.status = Run.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

    monkeypatch.setattr(sched_tasks, "execute_run", _fake_execute)
    sched_tasks.execute_scheduled_run(sr.id, hist.id)

    caps = list(Run.objects.filter(user=user).values_list("max_budget_usd", flat=True))
    assert caps and all(c is not None for c in caps), (
        f"child runs carry no hard budget (max_budget_usd={caps}); "
        "the $1.00 ceiling only gates the pre-flight estimate"
    )


# =============================================================================
# F8 — Cron validation gaps.
# =============================================================================


@pytest.mark.django_db
def test_f8a_never_firing_cron_is_rejected_not_500(client, attacker):
    wl = _watchlist(attacker)
    before = ScheduledRun.objects.count()
    try:
        resp = client.post(
            "/api/scheduled-runs/",
            {"name": "feb31", "watchlist": wl.id, "cron_expression": "0 0 31 2 *",
             "is_market_aware": False},
            format="json",
        )
        status = resp.status_code
    except Exception as exc:  # noqa: BLE001 — the view leaks CroniterBadDateError (→ 500)
        status = f"unhandled {type(exc).__name__}"
    orphan_rows = ScheduledRun.objects.count() - before
    assert status == 400 and orphan_rows == 0, (
        f"'0 0 31 2 *' passed is_valid_cron, then compute_next blew up: status={status}, "
        f"orphan rows persisted with next_run_at=None: {orphan_rows}"
    )


@pytest.mark.django_db
def test_f8b_six_field_cron_is_rejected(client, attacker):
    """croniter reads a 6th field as SECONDS (last); cron_descriptor reads 6 fields
    as Quartz (seconds FIRST). The stored description contradicts next_run_at."""
    wl = _watchlist(attacker)
    resp = client.post(
        "/api/scheduled-runs/",
        {"name": "six", "watchlist": wl.id, "cron_expression": "30 16 * * 5 0",
         "timezone": "America/New_York", "is_market_aware": False},
        format="json",
    )
    assert resp.status_code == 400, (
        f"6-field cron accepted; description={resp.json().get('cron_description')!r} "
        f"vs next_run_at={resp.json().get('next_run_at')!r}"
    )


@pytest.mark.django_db
def test_f8c_every_minute_cron_is_rejected(client, attacker):
    wl = _watchlist(attacker, ["AAPL", "MSFT", "NVDA"])
    resp = client.post(
        "/api/scheduled-runs/",
        {"name": "storm", "watchlist": wl.id, "cron_expression": "* * * * *",
         "is_market_aware": False},
        format="json",
    )
    assert resp.status_code == 400, (
        "a 1-minute cadence (3 council runs every minute, 4320 runs/day) is accepted"
    )


# =============================================================================
# F9 — ScheduledRun payload fields with no validation.
# =============================================================================


@pytest.mark.django_db
def test_f9_schedule_rejects_unknown_agents_personas_and_non_string_models(client, attacker):
    wl = _watchlist(attacker)
    resp = client.post(
        "/api/scheduled-runs/",
        {
            "name": "junk", "watchlist": wl.id, "cron_expression": "0 9 * * 1-5",
            "is_market_aware": False,
            "model_overrides": {"typo_agent": "openrouter:x", "cio": 12345},
            "personas": ["nobody"],
        },
        format="json",
    )
    assert resp.status_code == 400, (
        f"junk accepted: overrides={resp.json().get('model_overrides')} "
        f"personas={resp.json().get('personas')}"
    )


@pytest.mark.django_db
def test_f9_max_orders_per_day_zero_means_zero_not_unlimited(user, monkeypatch):
    from apps.brokers.adapters import mock as mock_adapter
    from apps.brokers.models import BrokerAccount, BrokerOrder
    from apps.portfolios.models import Portfolio
    from apps.runs.models import Decision
    from apps.schedules import autosubmit
    from apps.schedules.autosubmit import auto_submit_orders

    # Keep the test off the network (the real _last_close calls FMP).
    monkeypatch.setattr(autosubmit, "_last_close", lambda user, ticker: Decimal("100"))
    mock_adapter.reset_state()
    pf = Portfolio.objects.create(
        user=user, name="Broker book", kind=Portfolio.KIND_BROKER, cash_balance=Decimal("100000")
    )
    acc = BrokerAccount.objects.create(
        user=user, broker="mock", mode=BrokerAccount.MODE_PAPER, account_id=f"demo-{user.id}",
        label="Paper", portfolio=pf, connection_status=BrokerAccount.STATUS_ACTIVE,
    )
    mock_adapter.seed_demo_book(acc, cash=Decimal("100000"))
    sr = ScheduledRun.objects.create(
        user=user, name="s", watchlist=_watchlist(user), cron_expression="25 9 * * 1-5",
        auto_submit_broker_account=acc, auto_paper_submit=True, auto_submit_draft_only=True,
        max_orders_per_day=0,  # a user writes 0 meaning "no orders"
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())
    run_ids = []
    for tk in ("AAPL", "MSFT"):
        run = Run.objects.create(user=user, tickers=[tk], as_of_date=dt.date(2026, 5, 1),
                                 status=Run.DONE)
        Decision.objects.create(run=run, ticker=tk, action="buy", confidence=80,
                                target_quantity=Decimal("10"))
        run_ids.append(run.id)
    out = auto_submit_orders(sr, hist, run_ids)
    mock_adapter.reset_state()
    assert out["submitted"] == 0 and BrokerOrder.objects.count() == 0, (
        f"max_orders_per_day=0 is treated as 'no cap': {out['submitted']} orders created"
    )


# =============================================================================
# F10 — DRF browsable API (HTML renderer) is enabled in every environment.
# =============================================================================


def test_f10_browsable_api_renderer_not_enabled():
    from rest_framework.settings import api_settings

    names = [c.__name__ for c in api_settings.DEFAULT_RENDERER_CLASSES]
    assert "BrowsableAPIRenderer" not in names, names


@pytest.mark.django_db
def test_f10_health_returns_json_to_browsers():
    resp = APIClient().get("/api/health/", HTTP_ACCEPT="text/html")
    assert resp["Content-Type"].startswith("application/json"), resp["Content-Type"]
    assert b"Unauthenticated liveness probe" not in resp.content  # view docstring rendered


# =============================================================================
# F11 — A malformed saved ollama_host 500s every catalog read for that user
#       (httpx.InvalidURL is not an httpx.HTTPError subclass).
# =============================================================================


@pytest.mark.django_db
def test_f11_models_list_survives_malformed_ollama_host(client):
    assert client.put("/api/me/provider-keys/", {"ollama_host": "http://[::1"},
                      format="json").status_code == 200
    try:
        resp = client.get("/api/models/")
        status = resp.status_code
    except httpx.InvalidURL as exc:
        status = f"unhandled httpx.InvalidURL: {exc}"
    assert status == 200, status


# =============================================================================
# F12 — Instance-wide catalog mutation endpoints are open to any authenticated
#       (non-staff) user, unlike the sibling tier editor (IsAdminUser).
# =============================================================================


@pytest.mark.django_db
def test_f12_catalog_sync_and_verify_require_staff(client):
    # The migration seeds this row; make sure it is active before the attack.
    row, _ = ModelEntry.objects.update_or_create(
        id="openrouter:meta-llama/llama-3.3-70b-instruct",
        defaults={"provider": "openrouter", "display_name": "Llama", "tier": "hosted_open",
                  "price_in_per_mtok": Decimal("0.1"), "price_out_per_mtok": Decimal("0.3"),
                  "is_active": True, "last_verified_note": ""},
    )
    with respx.mock(assert_all_called=False) as m:
        m.get("https://openrouter.ai/api/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [
                {"id": "some/other-model", "pricing": {"prompt": "0", "completion": "0"}}
            ]})
        )
        fetch = client.post("/api/models/fetch/", {}, format="json")
        verify = client.post("/api/models/verify-pricing/", {}, format="json")
    row = ModelEntry.objects.get(id="openrouter:meta-llama/llama-3.3-70b-instruct")
    assert fetch.status_code == 403 and verify.status_code == 403, (
        f"non-staff user ran the sync ({fetch.status_code}) / verify ({verify.status_code}); "
        f"catalog row is now is_active={row.is_active}, note={row.last_verified_note!r}"
    )


# =============================================================================
# F13 — No CACHES configured → LocMemCache (per-process). operator.py uses it as a
#       cross-worker throttle/cooldown, which cannot work under prefork.
# =============================================================================


def test_f13_operator_alert_throttle_uses_a_shared_cache():
    backend = settings.CACHES["default"]["BACKEND"]
    assert "locmem" not in backend, (
        f"{backend}: record_provider_failure() strikes/cooldown are per-process; "
        "with --concurrency=N each worker child keeps its own counter"
    )


# =============================================================================
# F15 — cron_description (schedule tz, no tz label) vs next_run_at (UTC, rendered in
#       the browser tz) — the numbers the user reported, reproduced.
# =============================================================================


def test_f15_cron_description_and_next_run_disagree_without_a_timezone_label():
    from zoneinfo import ZoneInfo

    from apps.schedules.triggers import compute_next, describe_cron

    expr, tz = "30 16 * * 5", "America/New_York"
    nxt = compute_next(expr, tz, after=dt.datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert describe_cron(expr) == "At 04:30 PM, only on Friday"
    assert nxt == dt.datetime(2026, 9, 11, 20, 30, tzinfo=ZoneInfo("UTC"))
    # Rendered by the fund dashboard with Angular `date:'EEE HH:mm'` in the viewer's
    # zone (Asia/Jerusalem = UTC+3 → "Fri 23:30") next to "At 04:30 PM" with no zone.
    assert nxt.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%a %H:%M") == "Fri 23:30"
    # …so the description the API serves must name the zone it is stated in.
    desc = describe_cron(expr, tz)
    assert desc == "At 04:30 PM, only on Friday (America/New_York)"
    assert tz in desc, "description carries no timezone; the two strings look contradictory"


@pytest.mark.django_db
def test_f15_serializer_cron_description_names_the_schedule_timezone(client, attacker):
    wl = _watchlist(attacker)
    resp = client.post(
        "/api/scheduled-runs/",
        {"name": "fri", "watchlist": wl.id, "cron_expression": "30 16 * * 5",
         "timezone": "America/New_York", "is_market_aware": False},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert resp.json()["cron_description"] == (
        "At 04:30 PM, only on Friday (America/New_York)"
    )


# =============================================================================
# F16 — Telegram bot tokens are stored in plaintext JSON (provider keys are Fernet
#       encrypted; the channel vault is not).
# =============================================================================


@pytest.mark.django_db
def test_f16_channel_secret_encrypted_at_rest(client, attacker):
    token = "999999:PLAINTEXT-IN-DB"
    resp = client.post(
        "/api/notification-channels/",
        {"kind": "telegram", "config": {"bot_token": token, "chat_id": "1"}},
        format="json",
    )
    assert resp.status_code == 201
    raw = NotificationChannel.objects.get(pk=resp.json()["id"]).config
    assert raw.get("bot_token") != token, (
        "bot_token stored unencrypted in NotificationChannel.config"
    )
