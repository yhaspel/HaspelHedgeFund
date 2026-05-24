"""Budget kill-switch: prime_agent_cache aborts when total_cost_usd >= cap.

We monkeypatch graph.invoke + record_llm_call to simulate cost accrual without
hitting any LLM provider.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.test import override_settings

from apps.accounts.models import User
from apps.backtests import engine as engine_mod
from apps.backtests.exceptions import BudgetExceeded
from apps.backtests.models import Backtest
from apps.backtests.tasks import run_backtest

pytestmark = pytest.mark.django_db


def _make_bt(cap: Decimal) -> Backtest:
    user = User.objects.create_user(email="b@x.com", password="x")
    return Backtest.objects.create(
        user=user, name="cap test",
        universe=["AAA", "BBB"],
        start_date=dt.date(2025, 1, 6), end_date=dt.date(2025, 1, 10),
        starting_cash=Decimal("100000"),
        is_window_days=5, oos_window_days=2, step_days=2,
        n_candidates=1, max_budget_usd=cap,
    )


@override_settings(BACKTEST_PRIME_PARALLELISM=1)
def test_prime_agent_cache_aborts_when_budget_exhausted(monkeypatch):
    # Force serial prime so the "exactly 2 invocations" assertion is
    # deterministic. The parallel path's budget kill-switch over-shoots by up
    # to max_workers-1 already-running futures; that's tested separately.
    bt = _make_bt(cap=Decimal("0.50"))

    # Pretend the universe has bars on these dates so trading_days returns them.
    days = [dt.date(2025, 1, 6), dt.date(2025, 1, 7), dt.date(2025, 1, 8)]
    monkeypatch.setattr(engine_mod, "trading_days", lambda *a, **k: days)

    # Pretend rebalance is daily.
    monkeypatch.setattr(
        engine_mod, "rebalance_dates_for", lambda days_, freq: set(days_),
    )

    # Fake graph: each invoke bumps the backtest's total_cost_usd by $0.30.
    invocations = {"n": 0}

    class FakeGraph:
        def invoke(self, state):
            invocations["n"] += 1
            Backtest.objects.filter(pk=bt.pk).update(
                total_cost_usd=Decimal("0.30") * invocations["n"]
            )
            return {}

    monkeypatch.setattr(
        "hedgefund_agents.graphs.council.build_council_graph",
        lambda personas=None: FakeGraph(),
    )
    monkeypatch.setattr(
        "hedgefund_agents.versioning.ensure_versions_synced", lambda: None
    )
    monkeypatch.setattr(
        "hedgefund_agents.versioning.snapshot_versions", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        "apps.data.providers.factory.get_edgar_provider",
        lambda *a, **kw: object(),
    )
    # close_old_connections() inside the loop tears down the test transaction.
    monkeypatch.setattr("django.db.close_old_connections", lambda: None)

    with pytest.raises(BudgetExceeded) as exc_info:
        engine_mod.prime_agent_cache(
            bt=bt, start=days[0], end=days[-1], rebalance_freq="daily",
        )
    # After 2 invocations spent = $0.60 >= cap $0.50; third invocation blocked.
    assert exc_info.value.cap == 0.50
    assert exc_info.value.spent >= 0.50
    assert invocations["n"] == 2


def test_run_backtest_task_sets_aborted_budget_status(monkeypatch):
    bt = _make_bt(cap=Decimal("4.00"))

    def boom(_bt):
        raise BudgetExceeded(spent=4.5, cap=4.0, done=10, total=100)

    monkeypatch.setattr("apps.backtests.walkforward.run_walkforward", boom)

    with pytest.raises(BudgetExceeded):
        run_backtest(bt.pk)
    bt.refresh_from_db()
    assert bt.status == Backtest.ABORTED_BUDGET
    assert "BudgetExceeded" in bt.error_message


@override_settings(BACKTEST_PRIME_PARALLELISM=4)
def test_prime_agent_cache_fans_out_across_threads(monkeypatch):
    """Regression: prime_agent_cache must dispatch ticker-day invocations to
    multiple threads in parallel, not loop them serially. Pre-fix: a nested
    `for day: for ticker:` ran sequentially in one worker — 3-year × 20-ticker
    primes took days. Post-fix: ThreadPoolExecutor with
    settings.BACKTEST_PRIME_PARALLELISM threads.

    We assert observed concurrency directly: a barrier in the fake graph
    requires N threads to be in `invoke` simultaneously before any can
    proceed. If the prime is serial, this deadlocks (caught by timeout).
    """
    import threading

    bt = _make_bt(cap=Decimal("100.00"))  # huge cap so budget never trips
    days = [dt.date(2025, 1, 6), dt.date(2025, 1, 7)]  # 2 days × 2 tickers = 4 work items
    monkeypatch.setattr(engine_mod, "trading_days", lambda *a, **k: days)
    monkeypatch.setattr(engine_mod, "rebalance_dates_for", lambda d, f: set(d))

    barrier = threading.Barrier(4, timeout=10)  # blow up if we don't see 4 concurrent threads
    invocations = {"n": 0}
    invocations_lock = threading.Lock()

    class BarrierGraph:
        def invoke(self, state):
            with invocations_lock:
                invocations["n"] += 1
            barrier.wait()  # all 4 threads must be here before any proceeds
            return {"buffett": {"action": "hold", "confidence": 0.5}}

    monkeypatch.setattr(
        "hedgefund_agents.graphs.council.build_council_graph",
        lambda personas=None: BarrierGraph(),
    )
    monkeypatch.setattr(
        "hedgefund_agents.versioning.ensure_versions_synced", lambda: None
    )
    monkeypatch.setattr(
        "hedgefund_agents.versioning.snapshot_versions", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        "apps.data.providers.factory.get_fmp_provider", lambda *a, **kw: object()
    )
    monkeypatch.setattr(
        "apps.data.providers.factory.get_edgar_provider", lambda *a, **kw: object()
    )
    monkeypatch.setattr("django.db.close_old_connections", lambda: None)

    cache = engine_mod.prime_agent_cache(
        bt=bt, start=days[0], end=days[-1], rebalance_freq="daily",
    )

    assert invocations["n"] == 4  # 2 days × 2 tickers
    assert len(cache) == 4
    # Each (ticker, day) key produced an agent-output entry.
    for d in days:
        for t in ("AAA", "BBB"):
            assert (t, d) in cache


@override_settings(BACKTEST_PRIME_PARALLELISM=1)
def test_prime_agent_cache_aborts_on_model_unavailable(monkeypatch):
    """Regression for the bt13 storm.

    Pre-fix: a permanently-broken model (HTTP 402 "Out of credits", 401 bad
    key, 404 wrong slug) silently failed every ticker-day as a graph.invoke
    exception and counted toward `failures`. The run would grind through
    thousands of guaranteed-doomed invocations before hitting
    `prime_min_completeness` and aborting with ABORTED_PARTIAL — wasting time
    on a config error.

    Post-fix: the OpenRouter adapter raises ModelUnavailable on
    401/402/403/404; prime_agent_cache catches the first one and aborts the
    entire run with the upstream error text intact.
    """
    from apps.backtests.exceptions import ModelUnavailable

    bt = _make_bt(cap=Decimal("100.00"))

    days = [dt.date(2025, 1, 6), dt.date(2025, 1, 7), dt.date(2025, 1, 8)]
    monkeypatch.setattr(engine_mod, "trading_days", lambda *a, **k: days)
    monkeypatch.setattr(engine_mod, "rebalance_dates_for", lambda d, f: set(d))

    invocations = {"n": 0}

    class FailingGraph:
        def invoke(self, state):
            invocations["n"] += 1
            raise ModelUnavailable(
                model="openrouter:dead/model:free",
                status_code=402,
                body='{"error":{"message":"Out of credits. Top up to continue.","code":402}}',
            )

    monkeypatch.setattr(
        "hedgefund_agents.graphs.council.build_council_graph",
        lambda personas=None: FailingGraph(),
    )
    monkeypatch.setattr("hedgefund_agents.versioning.ensure_versions_synced", lambda: None)
    monkeypatch.setattr("hedgefund_agents.versioning.snapshot_versions", lambda *_a, **_k: {})
    monkeypatch.setattr("apps.data.providers.factory.get_fmp_provider", lambda *a, **kw: object())
    monkeypatch.setattr("apps.data.providers.factory.get_edgar_provider", lambda *a, **kw: object())
    monkeypatch.setattr("django.db.close_old_connections", lambda: None)

    with pytest.raises(ModelUnavailable) as exc_info:
        engine_mod.prime_agent_cache(
            bt=bt, start=days[0], end=days[-1], rebalance_freq="daily",
        )
    # Abort on the first failing invocation — do NOT grind through all 6.
    assert invocations["n"] == 1
    assert exc_info.value.status_code == 402
    assert exc_info.value.model == "openrouter:dead/model:free"
    assert "Out of credits" in exc_info.value.body


def test_run_backtest_task_sets_failed_with_clear_message_on_model_unavailable(monkeypatch):
    """Orchestrator must surface ModelUnavailable as a clear FAILED status
    with the upstream error in error_message so users see *why* without
    having to grep worker logs."""
    from apps.backtests.exceptions import ModelUnavailable

    bt = _make_bt(cap=Decimal("4.00"))

    def boom(_bt):
        raise ModelUnavailable(
            model="openrouter:dead/model:free",
            status_code=402,
            body='Out of credits',
        )

    monkeypatch.setattr("apps.backtests.walkforward.run_walkforward", boom)

    with pytest.raises(ModelUnavailable):
        run_backtest(bt.pk)
    bt.refresh_from_db()
    assert bt.status == Backtest.FAILED
    assert "ModelUnavailable" in bt.error_message
    assert "HTTP 402" in bt.error_message
    assert "dead/model" in bt.error_message


@override_settings(BACKTEST_PRIME_PARALLELISM=1)
def test_disable_cio_field_flows_into_graph_state(monkeypatch):
    """Regression: Backtest.disable_cio was hardcoded True in engine.py. The
    field is now persisted on the model and read at dispatch time, so a user
    can opt CIO into a backtest pipeline per-run (e.g. when validating that
    the live council shape matches the backtest council shape)."""
    bt = _make_bt(cap=Decimal("100.00"))
    bt.disable_cio = False  # opt CIO IN for this backtest
    bt.save(update_fields=["disable_cio"])

    days = [dt.date(2025, 1, 6)]
    monkeypatch.setattr(engine_mod, "trading_days", lambda *a, **k: days)
    monkeypatch.setattr(engine_mod, "rebalance_dates_for", lambda d, f: set(d))

    seen_states: list[dict] = []

    class CaptureGraph:
        def invoke(self, state):
            seen_states.append(dict(state))
            return {"buffett": {"action": "hold"}}

    monkeypatch.setattr(
        "hedgefund_agents.graphs.council.build_council_graph",
        lambda personas=None: CaptureGraph(),
    )
    monkeypatch.setattr("hedgefund_agents.versioning.ensure_versions_synced", lambda: None)
    monkeypatch.setattr("hedgefund_agents.versioning.snapshot_versions", lambda *_a, **_k: {})
    monkeypatch.setattr("apps.data.providers.factory.get_fmp_provider", lambda *a, **kw: object())
    monkeypatch.setattr("apps.data.providers.factory.get_edgar_provider", lambda *a, **kw: object())
    monkeypatch.setattr("django.db.close_old_connections", lambda: None)

    engine_mod.prime_agent_cache(
        bt=bt, start=days[0], end=days[0], rebalance_freq="daily",
    )

    # Both ticker-day invocations must have received disable_cio=False from bt.
    assert len(seen_states) == 2
    for s in seen_states:
        assert s["disable_cio"] is False

    # And the inverse: a fresh backtest still defaults to disable_cio=True.
    bt2 = Backtest.objects.create(
        user=bt.user, name="default cio test",
        universe=["AAA", "BBB"],
        start_date=days[0], end_date=days[0],
        starting_cash=Decimal("100000"),
        is_window_days=5, oos_window_days=2, step_days=2,
        n_candidates=1, max_budget_usd=Decimal("100.00"),
    )
    assert bt2.disable_cio is True  # model default unchanged
    seen_states.clear()
    engine_mod.prime_agent_cache(
        bt=bt2, start=days[0], end=days[0], rebalance_freq="daily",
    )
    for s in seen_states:
        assert s["disable_cio"] is True
