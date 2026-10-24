"""Credential failures are loud, and dead keys / stale bars surface early.

Covers:
  * a 401 raises ProviderAuthError at once (no ladder walk, no paid hatch,
    no quarantine), naming the provider + key source, never the key; the key
    source is threaded from registry.get_llm / the FMP factory, and a stored
    BYO key that will not decrypt is reported as the platform key it falls to;
  * refresh_universe_bars names its per-ticker failures and the freshness
    gate (and the autopilot skip alert) lead with that cause;
  * fund/autopilot alerts and the new ops alerts skip the daily cap;
  * a scheduled fire whose child runs all fail is recorded failed;
  * the provider-key probe alerts once per outage per key fingerprint;
  * the post-close bar refresh alerts on stale pods and never trades;
  * the :free last resort skips a slug the catalog has retired;
  * the New York-time beat entries for the probe and the refresh.
All network is mocked.
"""
from __future__ import annotations

import datetime as dt
import logging
import types
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.utils import timezone

from apps.backtests.exceptions import ModelUnavailable, ProviderAuthError
from apps.notifications.models import NotificationChannel, NotificationEvent
from hedgefund_agents.llm.client import Message

User = get_user_model()
NY = ZoneInfo("America/New_York")


def _resp(status: int, payload: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    r.text = text
    r.request = MagicMock()
    return r


def _ok() -> MagicMock:
    return _resp(
        200,
        {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


def _seq_http(*responses: MagicMock) -> MagicMock:
    http = MagicMock()
    http.post.side_effect = list(responses)
    return http


_USER_NOT_FOUND = '{"error":{"message":"User not found.","code":401}}'


# --- a 401 rejects the key, not the model -------------------------------------


@pytest.mark.parametrize("paid_hatch", [False, True])
def test_openrouter_401_raises_provider_auth_error_without_walking_the_ladder(
    settings, paid_hatch
):
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.OPENROUTER_PAID_FALLBACK = paid_hatch
    http = _seq_http(*[_resp(401, text=_USER_NOT_FOUND) for _ in range(8)])
    client = OpenRouterClient(api_key="sk-or-v1-test-dead", http=http, key_source="user")
    with patch("apps.models_catalog.quarantine.quarantine_model") as quarantine:
        with pytest.raises(ProviderAuthError) as ei:
            client.complete(
                model="nvidia/nemotron-3.5-super-vl:free", messages=[Message("user", "hi")]
            )
    assert http.post.call_count == 1  # no fallback hop, no paid escalation
    quarantine.assert_not_called()
    err = ei.value
    assert isinstance(err, ModelUnavailable)  # existing handlers still abort once
    assert err.status_code == 401 and err.key_source == "user"
    assert err.model == "nvidia/nemotron-3.5-super-vl:free"
    msg = str(err)
    assert "OpenRouter rejected the API key (HTTP 401, key_source=user)" in msg
    assert "Settings → Provider keys" in msg
    assert "User not found." in msg
    assert "sk-or-v1-test-dead" not in msg


def test_provider_auth_error_never_echoes_the_key_even_if_upstream_does():
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    key = "sk-or-v1-test-echoed"
    http = _seq_http(_resp(401, text=f'{{"error":"bad key {key}"}}'))
    with pytest.raises(ProviderAuthError) as ei:
        OpenRouterClient(api_key=key, http=http).complete(
            model="meta-llama/llama-3.3-70b-instruct", messages=[Message("user", "hi")]
        )
    assert key not in str(ei.value) and key not in ei.value.body
    assert "[redacted]" in str(ei.value)
    assert "key_source=platform" in str(ei.value)
    assert "server environment" in str(ei.value)


@pytest.mark.django_db
def test_401_mid_chain_stops_the_chain_and_quarantines_nothing(settings):
    """A dead route (404) hops; if the next rung answers 401 the chain stops
    there, and the 404'd route is NOT quarantined (no later rung succeeded)."""
    from apps.models_catalog.models import ModelEntry
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    first = DEV_TIER_SLUGS[0]
    calls: list[str] = []

    def fake_post(self, body, headers):
        calls.append(body["model"])
        if len(calls) == 1:
            return httpx.Response(
                404, text="Function not found",
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
            )
        return httpx.Response(
            401, text=_USER_NOT_FOUND,
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
        )

    with patch.object(OpenRouterClient, "_post_with_retry", fake_post):
        with pytest.raises(ProviderAuthError):
            OpenRouterClient(api_key="sk-or-v1-test").complete(model=first, messages=[])
    assert len(calls) == 2
    assert ModelEntry.objects.get(id=f"openrouter:{first}").is_active is True


@pytest.mark.parametrize("status", [402, 403, 404])
def test_402_403_404_still_walk_the_fallback_chain(settings, status):
    from hedgefund_agents.llm.adapters.openrouter import OpenRouterClient

    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.LLM_LAST_RESORT_MODEL = "meta-llama/llama-3.3-70b-instruct"
    settings.LLM_FREE_ONLY = False
    http = _seq_http(_resp(status, text="nope"), _ok())
    resp = OpenRouterClient(api_key="sk-or-v1-test", http=http).complete(
        model="some/paid-route", messages=[Message("user", "hi")]
    )
    assert resp.text == '{"ok":true}'
    assert http.post.call_count == 2


@pytest.mark.django_db
def test_anthropic_401_raises_provider_auth_error(settings):
    from hedgefund_agents.llm.adapters.anthropic import AnthropicClient

    settings.BLOCK_ANTHROPIC = False
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error"}}
        )

    client = AnthropicClient(
        api_key="sk-ant-test",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        key_source="user",
    )
    with pytest.raises(ProviderAuthError) as ei:
        client.complete(model="claude-haiku-4-5-20251001", messages=[Message("user", "x")])
    assert calls["n"] == 1
    assert "Anthropic rejected the API key (HTTP 401, key_source=user)" in str(ei.value)
    assert "sk-ant-test" not in str(ei.value)


@pytest.mark.django_db
def test_backtest_abort_progress_names_the_key_not_a_model(monkeypatch, settings):
    from apps.backtests import engine as engine_mod
    from apps.backtests.models import Backtest

    settings.BACKTEST_PRIME_PARALLELISM = 1
    user = User.objects.create_user(email="bt-key@x.test", password="x" * 12)
    bt = Backtest.objects.create(
        user=user, name="dead key", universe=["AAA"],
        start_date=dt.date(2025, 1, 6), end_date=dt.date(2025, 1, 8),
        starting_cash=Decimal("100000"), is_window_days=5, oos_window_days=2,
        step_days=2, n_candidates=1, max_budget_usd=Decimal("10"),
    )
    days = [dt.date(2025, 1, 6), dt.date(2025, 1, 7)]
    monkeypatch.setattr(engine_mod, "trading_days", lambda *a, **k: days)
    monkeypatch.setattr(engine_mod, "rebalance_dates_for", lambda d, f: set(d))

    class DeadKeyGraph:
        def invoke(self, state):
            raise ProviderAuthError(
                "OpenRouter", "nvidia/nemotron-3.5-super-vl:free", key_source="user"
            )

    monkeypatch.setattr(
        "hedgefund_agents.graphs.council.build_council_graph",
        lambda personas=None: DeadKeyGraph(),
    )
    monkeypatch.setattr("hedgefund_agents.versioning.ensure_versions_synced", lambda: None)
    monkeypatch.setattr("hedgefund_agents.versioning.snapshot_versions", lambda *_a, **_k: {})
    monkeypatch.setattr("apps.data.providers.factory.get_fmp_provider", lambda *a, **kw: object())
    monkeypatch.setattr("apps.data.providers.factory.get_edgar_provider", lambda *a, **kw: object())
    notes: list[str] = []
    with pytest.raises(ProviderAuthError):
        engine_mod.prime_agent_cache(
            bt=bt, start=days[0], end=days[-1], rebalance_freq="daily",
            progress_cb=lambda done, total, note: notes.append(note),
        )
    assert notes == ["aborted: provider rejected the API key"]


def test_node_wrapper_reraises_provider_auth_error():
    from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant

    def node(state):
        raise ProviderAuthError("OpenRouter", "m", key_source="user", body="")

    with pytest.raises(ProviderAuthError):
        wrap_backtest_tolerant(node, "buffett")({"ticker": "AAPL"})


@pytest.mark.django_db
def test_get_llm_threads_the_key_source(settings):
    from apps.models_catalog.models import ProviderKey
    from hedgefund_agents.registry import _make_client, get_llm

    _make_client.cache_clear()
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-platform"
    byok = User.objects.create_user(email="byok@x.test", password="x" * 12)
    pk = ProviderKey.objects.create(user=byok)
    pk.set_key("openrouter", "sk-or-v1-test-user")
    pk.save()
    plain = User.objects.create_user(email="plain@x.test", password="x" * 12)
    broken = User.objects.create_user(email="broken@x.test", password="x" * 12)
    ProviderKey.objects.create(user=broken, openrouter_api_key_enc="not-a-fernet-token")

    c = get_llm("openrouter", user_id=byok.id)
    assert (c.api_key, c.key_source) == ("sk-or-v1-test-user", "user")
    c = get_llm("openrouter", user_id=plain.id)
    assert (c.api_key, c.key_source) == ("sk-or-v1-test-platform", "platform")
    # The stored value will not decrypt → the platform key is what runs, and is
    # what a 401 must name.
    c = get_llm("openrouter", user_id=broken.id)
    assert (c.api_key, c.key_source) == ("sk-or-v1-test-platform", "platform")
    _make_client.cache_clear()


@pytest.mark.django_db
def test_fmp_factory_reports_the_key_source_actually_used(settings):
    from apps.data.providers import factory
    from apps.models_catalog.models import ProviderKey

    settings.ALLOW_PLATFORM_DATA_KEYS = True
    settings.FMP_API_KEY = "fmp-test-platform"
    factory._reset_caches_for_tests()
    byok = User.objects.create_user(email="fmp-byok@x.test", password="x" * 12)
    pk = ProviderKey.objects.create(user=byok)
    pk.set_key("fmp", "fmp-test-user")
    pk.save()
    broken = User.objects.create_user(email="fmp-broken@x.test", password="x" * 12)
    ProviderKey.objects.create(user=broken, fmp_api_key_enc="not-a-fernet-token")

    p = factory.get_fmp_provider(user=byok)
    assert (p.api_key, p.key_source) == ("fmp-test-user", "user")
    assert factory._resolve_data_key(broken, "fmp", "FMP_API_KEY") == ("", "platform")
    p = factory.get_fmp_provider(user=broken)
    assert (p.api_key, p.key_source) == ("fmp-test-platform", "platform")
    factory._reset_caches_for_tests()


# --- name the stale-data cause -------------------------------------------------


def _http_error(status: int, ticker: str) -> httpx.HTTPStatusError:
    req = httpx.Request(
        "GET",
        "https://financialmodelingprep.com/stable/historical-price-eod/full"
        f"?symbol={ticker}&apikey=fmp-test-SECRET",
    )
    return httpx.HTTPStatusError(
        f"Client error '{status}' for url '{req.url}'",
        request=req,
        response=httpx.Response(status, request=req),
    )


class _FailingProvider:
    """FMP stand-in: per-ticker upsert outcomes; dividends always fail alike."""

    key_source = "user"

    def __init__(self, outcomes: dict[str, Exception | None]):
        self.outcomes = outcomes

    def upsert_recent_bars(self, ticker, start, end):
        exc = self.outcomes.get(ticker)
        if exc is not None:
            raise exc
        return 0

    def get_dividends(self, ticker, start, end):
        exc = self.outcomes.get(ticker)
        if exc is not None:
            raise exc
        return []


@pytest.mark.django_db
def test_refresh_summary_names_the_cause_never_the_url():
    from apps.data.freshness import refresh_universe_bars

    tickers = ["SPY", "TLT", "GLD"]
    prov = _FailingProvider({t: _http_error(401, t) for t in tickers})
    summary = refresh_universe_bars(tickers, dt.date(2026, 10, 23), prov)
    assert summary == "FMP refresh failed for 3/3 tickers: HTTPStatusError 401 (key_source=user)"

    prov = _FailingProvider(
        {"SPY": _http_error(401, "SPY"), "TLT": httpx.ReadTimeout("slow"), "GLD": None}
    )
    summary = refresh_universe_bars(tickers, dt.date(2026, 10, 23), prov)
    assert summary == (
        "FMP refresh failed for 2/3 tickers: HTTPStatusError 401 (SPY); "
        "ReadTimeout (TLT) (key_source=user)"
    )
    assert "SECRET" not in summary and "://" not in summary
    assert refresh_universe_bars(["GLD"], dt.date(2026, 10, 23), _FailingProvider({})) == ""


@pytest.mark.django_db
def test_refresh_failures_are_logged_by_reason_never_the_url(caplog):
    """A worker logs through Celery's handlers, not the redacting Django ones, so
    a refresh failure is logged by its short reason — never an httpx traceback,
    whose message quotes the FMP URL and with it the API key."""
    from apps.data.freshness import refresh_universe_bars

    prov = _FailingProvider({"SPY": _http_error(401, "SPY")})
    with caplog.at_level(logging.WARNING, logger="apps.data.freshness"):
        refresh_universe_bars(["SPY"], dt.date(2026, 10, 23), prov)
    assert "upsert failed ticker=SPY (HTTPStatusError 401)" in caplog.text
    assert "dividend refresh failed ticker=SPY (HTTPStatusError 401)" in caplog.text
    assert not any(r.exc_info or r.exc_text for r in caplog.records)  # no traceback
    assert "SECRET" not in caplog.text and "apikey" not in caplog.text


@pytest.mark.django_db
def test_freshness_gate_leads_with_the_refresh_summary():
    from apps.data.freshness import StaleMarketDataError, assert_universe_fresh

    with pytest.raises(StaleMarketDataError) as ei:
        assert_universe_fresh(
            ["NOPE"], dt.date(2026, 10, 23),
            refresh_summary="FMP refresh failed for 1/1 tickers: HTTPStatusError 401",
        )
    assert str(ei.value).startswith(
        "FMP refresh failed for 1/1 tickers: HTTPStatusError 401. NOPE: no bars"
    )


def _rp_strategy(user, *, name="RiskParity", kind=None, enabled=True, channel=None):
    from apps.portfolios.models import (
        Portfolio,
        PortfolioStrategy,
        StrategyAutopilot,
        Universe,
        UniverseMembership,
    )

    u = Universe.objects.create(name=f"cfv-{name.lower()}")
    for t in ("SPY", "TLT", "GLD"):
        UniverseMembership.objects.create(
            universe=u, ticker=t, sector="X", effective_from=dt.date(2007, 1, 1),
        )
    pf = Portfolio.objects.create(
        user=user, kind=Portfolio.KIND_STRATEGY, name=name, cash_balance=Decimal("100000"),
    )
    strategy = PortfolioStrategy.objects.create(
        user=user, name=name, universe=u, portfolio=pf,
        kind=kind or PortfolioStrategy.KIND_RISK_PARITY, personas=[],
    )
    ap = StrategyAutopilot.objects.create(
        strategy=strategy, is_enabled=enabled, notification_channel=channel,
    )
    return strategy, ap


def _fill_daily_cap(channel, n: int = 10) -> None:
    for i in range(n):
        NotificationEvent.objects.create(
            channel=channel, subject=f"earlier {i}", delivery_status=NotificationEvent.SENT,
        )


@pytest.mark.django_db
def test_autopilot_stale_skip_alert_names_the_refresh_cause(monkeypatch, settings):
    """A dead FMP key 401s every refresh, the gate skips the pod, and the
    (cap-exempt) skip alert says WHY — not only "stale/corrupt"."""
    from apps.portfolios import autopilot_risk, tasks, tasks_autopilot
    from apps.portfolios.models import AutopilotRun

    settings.NOTIFICATIONS_MAX_PER_DAY = 10
    user = User.objects.create_user(email="ap-owner@x.test", password="x" * 12)
    ch = NotificationChannel.objects.create(
        user=user, kind=NotificationChannel.EMAIL, config={"address": "ap-owner@x.test"},
    )
    _fill_daily_cap(ch)  # a day of earlier pings must not silence this alert
    strategy, ap = _rp_strategy(user, channel=ch)
    run = AutopilotRun.objects.create(autopilot=ap, fire_time_utc=timezone.now())
    dead = _FailingProvider({t: _http_error(401, t) for t in ("SPY", "TLT", "GLD")})
    monkeypatch.setattr(tasks, "get_fmp_provider", lambda user=None: dead)
    monkeypatch.setattr(autopilot_risk, "evaluate_drawdown", lambda ap_: {})

    out = tasks_autopilot.run_autopilot_cycle(run.id)

    assert out["skipped"] == "stale_data"
    cause = "FMP refresh failed for 3/3 tickers: HTTPStatusError 401 (key_source=user)"
    run.refresh_from_db()
    assert run.status == AutopilotRun.SKIPPED
    assert run.submit_decision["detail"].startswith(cause)
    ev = NotificationEvent.objects.filter(channel=ch).latest("pk")
    assert ev.delivery_status == NotificationEvent.SENT
    assert "Cycle skipped — stale/corrupt market data; book held." in ev.body
    assert cause in ev.body
    assert "SECRET" not in ev.body and "SECRET" not in str(run.submit_decision)


# --- operational alerts skip the daily cap --------------------------------------


@pytest.mark.django_db
def test_daily_cap_exempt_skips_only_the_daily_cap(settings):
    from apps.notifications.services import send_notification

    settings.NOTIFICATIONS_MAX_PER_DAY = 10
    settings.NOTIFICATIONS_MAX_PER_TICKER_PER_DAY = 3
    user = User.objects.create_user(email="cap@x.test", password="x" * 12)
    ch = NotificationChannel.objects.create(
        user=user, kind=NotificationChannel.EMAIL, config={"address": "cap@x.test"},
    )
    _fill_daily_cap(ch)
    assert send_notification(ch, "s", "b").delivery_status == NotificationEvent.THROTTLED
    exempt = send_notification(ch, "s", "b", daily_cap_exempt=True)
    assert exempt.delivery_status == NotificationEvent.SENT
    for _ in range(3):
        NotificationEvent.objects.create(
            channel=ch, subject="t", ticker="AAPL", delivery_status=NotificationEvent.SENT,
        )
    per_ticker = send_notification(ch, "s", "b", ticker="AAPL", daily_cap_exempt=True)
    assert per_ticker.delivery_status == NotificationEvent.THROTTLED  # per-ticker cap stays


@pytest.mark.django_db
def test_fund_and_autopilot_state_alerts_bypass_the_daily_cap(settings):
    from apps.notifications import autopilot as ap_notify

    settings.NOTIFICATIONS_MAX_PER_DAY = 10
    user = User.objects.create_user(email="fund@x.test", password="x" * 12)
    ch = NotificationChannel.objects.create(
        user=user, kind=NotificationChannel.EMAIL, config={"address": "fund@x.test"},
    )
    _fill_daily_cap(ch)
    ap = types.SimpleNamespace(
        pk=1, notification_channel_id=ch.id, notification_channel=ch,
        strategy=types.SimpleNamespace(name="Pod", user=user),
    )

    def last_status() -> str:
        return NotificationEvent.objects.filter(channel=ch).latest("pk").delivery_status

    for event in (ap_notify.CYCLE_DONE, ap_notify.HALT, ap_notify.ACCOUNT_UNHEALTHY):
        assert ap_notify.notify_autopilot(ap, event, "summary")
        assert last_status() == NotificationEvent.SENT, event
    assert ap_notify.notify_autopilot(ap, ap_notify.FILL, "AAPL filled")
    assert last_status() == NotificationEvent.THROTTLED  # routine pings stay capped
    fund = types.SimpleNamespace(pk=1, name="Fund", owner=user)
    assert ap_notify.notify_fund(fund, "aggregate drawdown breached")
    assert last_status() == NotificationEvent.SENT


# --- scheduled-run history outcome ----------------------------------------------


def _schedule(user, tickers, channel=None):
    from apps.schedules.models import ScheduledRun, ScheduledRunHistory
    from apps.watchlists.models import Watchlist, WatchlistTicker

    wl = Watchlist.objects.create(user=user, name="W", is_default=True)
    for t in tickers:
        WatchlistTicker.objects.create(watchlist=wl, ticker=t)
    sr = ScheduledRun.objects.create(
        user=user, name="daily", watchlist=wl, cron_expression="25 9 * * 1-5",
        model_preset="hybrid", notification_channel=channel,
    )
    hist = ScheduledRunHistory.objects.create(scheduled_run=sr, fire_time_utc=timezone.now())
    return sr, hist


_AUTH_FAIL = (
    "ProviderAuthError: OpenRouter rejected the API key (HTTP 401, key_source=user) — "
    "check Settings → Provider keys. Upstream: User not found."
)


def _fake_execute(fail: set[str]):
    from apps.models_catalog.presets import PERSONA_AGENTS
    from apps.runs.models import AgentMessage, Decision, Run

    personas = sorted(PERSONA_AGENTS)

    def _run(run_id):
        run = Run.objects.get(pk=run_id)
        if run.tickers[0] in fail:
            run.status = Run.FAILED
            run.error_message = _AUTH_FAIL
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error_message", "finished_at"])
            raise ProviderAuthError("OpenRouter", "m", key_source="user", body="")
        for i in range(5):
            AgentMessage.objects.create(
                run=run, agent_name=personas[i],
                parsed_output={"signal": "bullish", "confidence": 80},
            )
        Decision.objects.create(
            run=run, ticker=run.tickers[0], action="buy", confidence=80,
            risk_overrides={"veto": False},
        )
        run.status = Run.DONE
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

    return _run


@pytest.mark.django_db
def test_schedule_fire_with_every_child_failed_is_recorded_failed(monkeypatch):
    from apps.schedules.models import ScheduledRunHistory
    from apps.schedules.tasks import execute_scheduled_run

    user = User.objects.create_user(email="sched-fail@x.test", password="x" * 12)
    sr, hist = _schedule(user, ["AAPL", "MSFT", "NVDA"])
    monkeypatch.setattr(
        "apps.schedules.tasks.execute_run", _fake_execute({"AAPL", "MSFT", "NVDA"})
    )
    out = execute_scheduled_run(sr.id, hist.id)
    hist.refresh_from_db()
    assert out["status"] == "failed" and out["failed"] == 3
    assert hist.status == ScheduledRunHistory.FAILED
    assert hist.error.startswith("3/3 child runs failed (")
    assert all(t in hist.error for t in ("AAPL", "MSFT", "NVDA"))
    assert f"): {_AUTH_FAIL}" in hist.error
    assert hist.notified_count == 0


@pytest.mark.django_db
def test_schedule_fire_records_children_that_fail_before_execute_runs_handler(settings):
    """execute_run resolves its data provider before its own failure handler: a
    user with no usable FMP key raises there, leaving the child RUNNING. The fire
    must still settle each child FAILED (and alert), and record itself failed."""
    from apps.data.providers import factory
    from apps.runs.models import Run
    from apps.schedules.models import ScheduledRunHistory
    from apps.schedules.tasks import execute_scheduled_run

    settings.ALLOW_PLATFORM_DATA_KEYS = False
    factory._reset_caches_for_tests()
    user = User.objects.create_user(email="sched-nokey@x.test", password="x" * 12)
    NotificationChannel.objects.create(
        user=user, kind=NotificationChannel.EMAIL, config={"address": "sched-nokey@x.test"},
    )
    sr, hist = _schedule(user, ["AAPL", "MSFT"])
    out = execute_scheduled_run(sr.id, hist.id)
    hist.refresh_from_db()
    children = list(Run.objects.filter(user=user))
    assert len(children) == 2 and {r.status for r in children} == {Run.FAILED}
    assert all(r.error_message.startswith("RuntimeError: No FMP key configured") for r in children)
    assert all(r.finished_at is not None for r in children)
    assert out["status"] == "failed" and out["failed"] == 2
    assert hist.status == ScheduledRunHistory.FAILED
    assert hist.error.startswith("2/2 child runs failed (")
    assert "): RuntimeError: No FMP key configured" in hist.error
    failed_alerts = NotificationEvent.objects.filter(subject__endswith="] failed")
    assert failed_alerts.count() == 2
    factory._reset_caches_for_tests()


@pytest.mark.django_db
def test_schedule_partial_failure_stays_done_names_failures_and_still_notifies(monkeypatch):
    from apps.schedules.models import ScheduledRunHistory
    from apps.schedules.tasks import execute_scheduled_run

    user = User.objects.create_user(email="sched-part@x.test", password="x" * 12)
    ch = NotificationChannel.objects.create(
        user=user, kind=NotificationChannel.EMAIL, config={"address": "me@x.test"},
    )
    sr, hist = _schedule(user, ["AAPL", "MSFT", "NVDA"], channel=ch)
    hist.error = "catch-up: 2 slots skipped"
    hist.save(update_fields=["error"])
    monkeypatch.setattr("apps.schedules.tasks.execute_run", _fake_execute({"MSFT"}))
    out = execute_scheduled_run(sr.id, hist.id)
    hist.refresh_from_db()
    assert out["status"] == "done" and out["failed"] == 1
    assert hist.status == ScheduledRunHistory.DONE
    assert hist.error == f"catch-up: 2 slots skipped; 1/3 child runs failed (MSFT): {_AUTH_FAIL}"
    assert hist.notified_count == 2  # the two successful tickers still notify
    assert len(mail.outbox) == 2


# --- provider-key probe ----------------------------------------------------------


@pytest.fixture
def operator(db):
    cache.clear()
    op = User.objects.create_user(email="op@x.test", password="x" * 12, is_superuser=True)
    NotificationChannel.objects.create(
        user=op, kind=NotificationChannel.EMAIL, config={"address": "op@x.test"},
    )
    yield op
    cache.clear()


def _probe_mock(m, *, openrouter: dict[str, object], fmp: dict[str, object]):
    """Route the two probe endpoints by key → status code (or an exception)."""

    def _answer(outcome, request):
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, json={"data": {}}, request=request)

    def _or(request):
        key = request.headers["Authorization"].removeprefix("Bearer ")
        return _answer(openrouter[key], request)

    def _fmp(request):
        return _answer(fmp[request.url.params["apikey"]], request)

    m.get("https://openrouter.ai/api/v1/key").mock(side_effect=_or)
    m.get(url__startswith="https://financialmodelingprep.com/stable/quote-short").mock(
        side_effect=_fmp
    )


def _alerts(op):
    return list(
        NotificationEvent.objects.filter(channel__user=op, subject__contains="rejected")
        .order_by("pk")
    )


def test_probe_checks_each_distinct_key_once_and_alerts_once_per_outage(operator, settings):
    from apps.models_catalog.key_probe import fingerprint
    from apps.models_catalog.models import ProviderKey
    from apps.models_catalog.tasks import probe_provider_keys

    settings.NOTIFICATIONS_MAX_PER_DAY = 10
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-platform"
    settings.FMP_API_KEY = "fmp-test-platform"
    _fill_daily_cap(NotificationChannel.objects.get(user=operator))
    # u1 shares the platform OpenRouter key; u2 has its own (dead) key.
    for email, or_key, fmp_key in (
        ("u1@x.test", "sk-or-v1-test-platform", ""),
        ("u2@x.test", "sk-or-v1-test-dead", "fmp-test-user"),
    ):
        pk = ProviderKey.objects.create(
            user=User.objects.create_user(email=email, password="x" * 12)
        )
        pk.set_key("openrouter", or_key)
        pk.set_key("fmp", fmp_key)
        pk.save()
    dead_fp = fingerprint("sk-or-v1-test-dead")
    routes = dict(
        openrouter={"sk-or-v1-test-platform": 200, "sk-or-v1-test-dead": 401},
        fmp={"fmp-test-platform": 200, "fmp-test-user": 200},
    )
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, **routes)
        out = probe_provider_keys()
        assert m.calls.call_count == 4  # 2 distinct OpenRouter keys + 2 FMP keys
    assert out["openrouter"] == {fingerprint("sk-or-v1-test-platform"): "ok", dead_fp: "rejected"}
    assert set(out["fmp"].values()) == {"ok"}
    alerts = _alerts(operator)
    assert len(alerts) == 1 and alerts[0].delivery_status == NotificationEvent.SENT
    assert dead_fp in alerts[0].body and "user #" in alerts[0].body
    assert "OpenRouter rejected an API key (HTTP 401)" in alerts[0].subject
    for ev in NotificationEvent.objects.all():
        assert "sk-or-v1-test" not in ev.body and "fmp-test" not in ev.body
    assert "sk-or-v1-test" not in str(out)

    # Still dead next probe: the outage is already open — no second alert.
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, **routes)
        probe_provider_keys()
    assert len(_alerts(operator)) == 1

    # Recovers, then dies again: a NEW outage alerts again.
    routes["openrouter"]["sk-or-v1-test-dead"] = 200
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, **routes)
        assert probe_provider_keys()["openrouter"][dead_fp] == "ok"
    routes["openrouter"]["sk-or-v1-test-dead"] = 403
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, **routes)
        probe_provider_keys()
    alerts = _alerts(operator)
    assert len(alerts) == 2 and "(HTTP 403)" in alerts[1].subject


def test_probe_never_reports_timeouts_or_server_errors_as_dead_keys(operator, settings):
    from apps.models_catalog.key_probe import fingerprint
    from apps.models_catalog.tasks import probe_provider_keys

    settings.OPENROUTER_API_KEY = "sk-or-v1-test-slow"
    settings.FMP_API_KEY = "fmp-test-slow"
    for or_outcome, fmp_outcome in (
        (httpx.ConnectTimeout("t"), 503),
        (500, 429),
        (httpx.ConnectError("down"), httpx.ReadTimeout("t")),
    ):
        with respx.mock(assert_all_called=False) as m:
            _probe_mock(
                m,
                openrouter={"sk-or-v1-test-slow": or_outcome},
                fmp={"fmp-test-slow": fmp_outcome},
            )
            out = probe_provider_keys()
        assert out["openrouter"] == {fingerprint("sk-or-v1-test-slow"): "inconclusive"}
        assert out["fmp"] == {fingerprint("fmp-test-slow"): "inconclusive"}
    assert _alerts(operator) == []

    # An open outage is not closed by an inconclusive probe either.
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, openrouter={"sk-or-v1-test-slow": 401}, fmp={"fmp-test-slow": 200})
        probe_provider_keys()
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, openrouter={"sk-or-v1-test-slow": 502}, fmp={"fmp-test-slow": 200})
        probe_provider_keys()
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, openrouter={"sk-or-v1-test-slow": 401}, fmp={"fmp-test-slow": 200})
        probe_provider_keys()
    assert len(_alerts(operator)) == 1


def test_probe_retries_an_alert_that_was_not_delivered(operator, settings):
    from apps.models_catalog.tasks import probe_provider_keys

    settings.OPENROUTER_API_KEY = "sk-or-v1-test-dead"
    settings.FMP_API_KEY = ""
    NotificationChannel.objects.filter(user=operator).update(is_active=False)
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, openrouter={"sk-or-v1-test-dead": 401}, fmp={})
        probe_provider_keys()  # no active operator channel: dropped, outage not latched
    NotificationChannel.objects.filter(user=operator).update(is_active=True)
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, openrouter={"sk-or-v1-test-dead": 401}, fmp={})
        probe_provider_keys()
    assert len(_alerts(operator)) == 1


def test_probe_retries_an_alert_whose_send_raised(operator, settings):
    from apps.models_catalog.key_probe import fingerprint
    from apps.models_catalog.tasks import probe_provider_keys
    from apps.notifications import operator as ops

    settings.OPENROUTER_API_KEY = "sk-or-v1-test-dead"
    settings.FMP_API_KEY = ""
    with patch.object(ops, "_operator_channels", side_effect=RuntimeError("db gone")):
        with respx.mock(assert_all_called=False) as m:
            _probe_mock(m, openrouter={"sk-or-v1-test-dead": 401}, fmp={})
            out = probe_provider_keys()
    assert out["openrouter"] == {fingerprint("sk-or-v1-test-dead"): "error"}
    with respx.mock(assert_all_called=False) as m:
        _probe_mock(m, openrouter={"sk-or-v1-test-dead": 401}, fmp={})
        probe_provider_keys()
    assert len(_alerts(operator)) == 1


# --- post-close bar refresh ------------------------------------------------------


def _bars(ticker: str, last: dt.date) -> None:
    from apps.data.models import DailyBar

    for back in range(3):
        d = last - dt.timedelta(days=back)
        DailyBar.objects.create(
            ticker=ticker, date=d, open=100, high=100, low=100,
            close=Decimal("100"), adjusted_close=Decimal("100"), volume=1, source="fmp",
        )


def test_post_close_refresh_alerts_once_on_stale_pods_and_never_trades(operator, monkeypatch):
    from apps.brokers.models import BrokerOrder
    from apps.portfolios.models import PortfolioStrategy, PortfolioTarget
    from apps.portfolios.tasks_autopilot import refresh_autopilot_bars

    owner = User.objects.create_user(email="pod-owner@x.test", password="x" * 12)
    today = timezone.now().astimezone(NY).date()
    for t in ("SPY", "TLT", "GLD"):
        _bars(t, today - dt.timedelta(days=8))  # a week stale
    stale, _ = _rp_strategy(owner, name="RP")
    _rp_strategy(owner, name="Off", enabled=False)
    _rp_strategy(owner, name="LS", kind=PortfolioStrategy.KIND_LONG_SHORT)
    dead = _FailingProvider({t: _http_error(401, t) for t in ("SPY", "TLT", "GLD")})
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider", lambda user=None: dead
    )

    out = refresh_autopilot_bars()

    assert out == {"checked": 1, "stale": 1}
    alerts = list(NotificationEvent.objects.filter(channel__user=operator))
    assert len(alerts) == 1
    body = alerts[0].body
    assert f"RP (#{stale.pk})" in body
    assert "FMP refresh failed for 3/3 tickers: HTTPStatusError 401 (key_source=user)" in body
    assert "8d stale" in body and "SECRET" not in body
    assert PortfolioTarget.objects.count() == 0 and BrokerOrder.objects.count() == 0


def test_post_close_refresh_tolerates_a_holiday_gap(operator, monkeypatch):
    from apps.portfolios.tasks_autopilot import refresh_autopilot_bars

    owner = User.objects.create_user(email="pod-owner2@x.test", password="x" * 12)
    today = timezone.now().astimezone(NY).date()
    for t in ("SPY", "TLT", "GLD"):
        _bars(t, today - dt.timedelta(days=4))  # e.g. a long holiday weekend
    _rp_strategy(owner)
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider",
        lambda user=None: _FailingProvider({}),
    )
    assert refresh_autopilot_bars() == {"checked": 1, "stale": 0}
    assert not NotificationEvent.objects.filter(channel__user=operator).exists()


# --- catalog-aware free last resort ----------------------------------------------


@pytest.mark.django_db
def test_free_last_resort_skips_a_slug_the_catalog_retired(settings):
    from apps.models_catalog.models import ModelEntry
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS
    from hedgefund_agents.llm.adapters.openrouter import _free_last_resort_model

    # The 2026-10-23 shape: the configured slug is delisted upstream and the
    # daily reconcile has deactivated its row.
    settings.LLM_LAST_RESORT_FREE_MODEL = "minimax/minimax-m3.1-flash:free"
    ModelEntry.objects.filter(id="openrouter:minimax/minimax-m3.1-flash:free").update(
        is_active=False
    )
    assert _free_last_resort_model() == DEV_TIER_SLUGS[0]
    ModelEntry.objects.filter(id=f"openrouter:{DEV_TIER_SLUGS[0]}").update(is_active=False)
    assert _free_last_resort_model() == DEV_TIER_SLUGS[2]  # [1] is the retired slug

    settings.LLM_LAST_RESORT_FREE_MODEL = "xingchen-agi/xing4.0-29b-a4b:free"
    assert _free_last_resort_model() == "xingchen-agi/xing4.0-29b-a4b:free"  # active: kept
    settings.LLM_LAST_RESORT_FREE_MODEL = "brand-new/route:free"
    assert _free_last_resort_model() == "brand-new/route:free"  # no row yet: kept


def _walk(orm, model: str) -> list[str]:
    tried, chain = (), []
    while (nxt := orm._next_heal_target(model, tried)) is not None:
        chain.append(nxt)
        tried, model = tried + (model,), nxt
    return chain


@pytest.mark.django_db
def test_free_chain_never_walks_onto_a_rung_the_catalog_retired(settings):
    """Once the live DB siblings are spent, the static :free ladder takes over —
    it must skip the retired rung too, so a chain whose every live rung failed
    ends (and names its error) on the last LIVE rung, not the delisted one."""
    from apps.models_catalog.models import ModelEntry
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS
    from hedgefund_agents.llm.adapters import openrouter as orm

    settings.LLM_SELF_HEAL = True
    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.LLM_FREE_ONLY = False
    retired = "minimax/minimax-m3.1-flash:free"
    ModelEntry.objects.filter(id=f"openrouter:{retired}").update(is_active=False)
    live = [s for s in DEV_TIER_SLUGS if s != retired]
    for start in DEV_TIER_SLUGS:
        chain = _walk(orm, start)
        assert retired not in chain, (start, chain)
    assert sorted(_walk(orm, DEV_TIER_SLUGS[0])) == sorted(live[1:])

    calls: list[str] = []

    def all_dead(self, body, headers):
        calls.append(body["model"])
        return httpx.Response(
            404, text="No endpoints found", request=httpx.Request("POST", orm.API_URL)
        )

    with patch.object(orm.OpenRouterClient, "_post_with_retry", all_dead):
        with pytest.raises(ModelUnavailable) as ei:
            orm.OpenRouterClient(api_key="sk-or-v1-test").complete(
                model=DEV_TIER_SLUGS[0], messages=[]
            )
    assert retired not in calls and sorted(calls) == sorted(live)
    assert ei.value.model == calls[-1]


@pytest.mark.django_db
def test_free_last_resort_is_reached_once_the_live_ladder_is_spent(settings):
    from apps.models_catalog.models import ModelEntry
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS
    from hedgefund_agents.llm.adapters import openrouter as orm

    settings.LLM_MAX_MODEL_FALLBACKS = 5
    settings.LLM_FREE_ONLY = False
    settings.LLM_LAST_RESORT_FREE_MODEL = "brand-new/route:free"
    ModelEntry.objects.filter(id="openrouter:minimax/minimax-m3.1-flash:free").update(
        is_active=False
    )
    assert _walk(orm, DEV_TIER_SLUGS[0])[-1] == "brand-new/route:free"
    # The whole curated ladder retired (the run-1178 shape): the catalog has
    # nothing live left, so the curated ladder is kept as the safety net.
    ModelEntry.objects.filter(id__in=[f"openrouter:{s}" for s in DEV_TIER_SLUGS]).update(
        is_active=False
    )
    assert orm._free_rungs() == (DEV_TIER_SLUGS, "brand-new/route:free")


def test_default_free_last_resort_is_a_priced_curated_rung():
    from apps.models_catalog.tier_menus import DEV_TIER_SLUGS
    from hedgefund_agents.llm.adapters.openrouter import (
        _DEFAULT_FREE_LAST_RESORT,
        is_reasoning_slug,
    )
    from hedgefund_agents.llm.pricing import PRICING

    assert _DEFAULT_FREE_LAST_RESORT in DEV_TIER_SLUGS
    assert _DEFAULT_FREE_LAST_RESORT != DEV_TIER_SLUGS[0]  # not the dev default's upstream
    assert PRICING[_DEFAULT_FREE_LAST_RESORT].input_per_mtok == 0.0
    assert not is_reasoning_slug(_DEFAULT_FREE_LAST_RESORT)


# --- beat entries ---------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,task,minute,hour,days",
    [
        ("probe-provider-keys-premarket", "apps.models_catalog.tasks.probe_provider_keys",
         40, 8, {1, 2, 3, 4, 5}),
        ("probe-provider-keys-friday", "apps.models_catalog.tasks.probe_provider_keys",
         0, 15, {5}),
        ("refresh-autopilot-bars-postclose",
         "apps.portfolios.tasks_autopilot.refresh_autopilot_bars", 30, 17, {1, 2, 3, 4, 5}),
    ],
)
def test_beat_entries_run_on_new_york_time(name, task, minute, hour, days):
    from django_celery_beat.models import CrontabSchedule

    from hedgefund.celery import app

    entry = app.conf.beat_schedule[name]
    assert entry["task"] == task
    sched = entry["schedule"]
    assert sched.tz == NY  # DST-proof: wall-clock New York, not fixed UTC
    assert (sched.minute, sched.hour, sched.day_of_week) == ({minute}, {hour}, days)
    # The DatabaseScheduler stores the zone with the row it syncs from this entry.
    assert str(CrontabSchedule.from_schedule(sched).timezone) == "America/New_York"
