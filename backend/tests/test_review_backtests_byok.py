"""Adversarial review (reviewer: backtests) — the council prime billed the
PLATFORM LLM key, never the user's BYOK key.

``prime_agent_cache._invoke_one`` built ``initial_state`` without ``user_id``,
unlike ``apps.runs.tasks`` which sets ``"user_id": run.user_id``.
``hedgefund_agents.registry.get_llm`` resolves the per-user ProviderKey ONLY
from ``state["user_id"]``, so every backtest LLM call fell back to
``settings.*_API_KEY``. Same for ``get_news_service(user=...)``.

FIXED by WP B3a §7: the prime state now carries the backtest owner's user id.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.backtests.engine import prime_agent_cache
from apps.backtests.models import Backtest
from apps.data.models import DailyBar
from apps.models_catalog.models import ProviderKey

User = get_user_model()
pytestmark = pytest.mark.django_db


class _Graph:
    def __init__(self) -> None:
        self.states: list[dict] = []

    def invoke(self, state: dict) -> dict:
        self.states.append(dict(state))
        return {"buffett": {"signal": "neutral", "confidence": 50}, "risk": {}, "valuation": {}}


def test_prime_state_carries_user_id_so_get_llm_uses_the_byok_key(settings):
    settings.BACKTEST_PRIME_PARALLELISM = 1
    settings.OPENROUTER_API_KEY = "platform-key-should-not-be-used"
    u = User.objects.create_user(email="byok@x.test", password="pw-fake-123456789")
    pk = ProviderKey.objects.create(user=u)
    pk.set_key("openrouter", "sk-or-USER-BYOK")
    pk.save()

    d = dt.date(2024, 1, 2)
    DailyBar.objects.create(
        ticker="AAA", date=d, source="fmp", open=1, high=1, low=1, close=1,
        adjusted_close=1, volume=1,
    )
    bt = Backtest.objects.create(
        user=u, name="byok", universe=["AAA"], start_date=d, end_date=d,
        personas=["buffett"], max_budget_usd=Decimal("0"),
    )
    assert bt.user_id == u.id
    g = _Graph()
    with patch("apps.graphs.compiler.resolve_graph", return_value=g), \
         patch("apps.data.providers.factory.get_fmp_provider", return_value=object()), \
         patch("apps.data.providers.factory.get_edgar_provider", return_value=object()), \
         patch("apps.data.providers.factory.get_ownership_provider", return_value=object()), \
         patch("hedgefund_agents.versioning.ensure_versions_synced"), \
         patch("hedgefund_agents.versioning.snapshot_versions", return_value={}):
        prime_agent_cache(bt=bt, start=d, end=d, rebalance_freq="daily")

    assert len(g.states) == 1
    state = g.states[0]
    assert state["user_id"] == u.id                     # <-- the fix
    assert state["backtest_id"] == bt.id

    # What every agent node then does with that state: the owner's BYOK key.
    from hedgefund_agents import registry

    with patch.object(registry, "_make_client", side_effect=lambda *a, **k: a) as mk:
        registry.get_llm("openrouter", state=state)
    _provider, user_id_arg, api_key, _host = mk.call_args.args
    assert user_id_arg == u.id and api_key == "sk-or-USER-BYOK"

    # Control: without user_id (the pre-fix state shape) it falls back to the
    # platform key — which is exactly what this backtest must never do.
    stripped = {k: v for k, v in state.items() if k != "user_id"}
    with patch.object(registry, "_make_client", side_effect=lambda *a, **k: a) as mk2:
        registry.get_llm("openrouter", state=stripped)
    _p, uid2, key2, _h = mk2.call_args.args
    assert uid2 is None and key2 == ""


class _SpendingGraph:
    """Simulates an agent graph whose calls bill the backtest heavily."""

    def __init__(self, bt_id: int) -> None:
        self.bt_id = bt_id
        self.calls = 0

    def invoke(self, state: dict) -> dict:
        from django.db.models import F
        self.calls += 1
        Backtest.objects.filter(pk=self.bt_id).update(
            total_cost_usd=F("total_cost_usd") + Decimal("50.00")
        )
        return {"buffett": {"signal": "neutral", "confidence": 50}, "risk": {}, "valuation": {}}


def test_max_budget_zero_is_refused_at_the_create_boundary():
    """`budget_cap = float(max_budget_usd or 0)` + `if budget_cap > 0:` in
    prime_agent_cache means a stored 0 is "no cap at all". Those engine
    semantics are unchanged (pre-existing rows must keep behaving as stored),
    so the fix is at the boundary: no NEW run can be created with the spend
    kill-switch disabled."""
    from types import SimpleNamespace

    from apps.backtests.serializers import BacktestCreateSerializer

    u = User.objects.create_user(email="budget-guard@x.test", password="pw-fake-123456789")
    base = {
        "name": "b", "universe": ["AAA"],
        "start_date": "2023-01-02", "end_date": "2025-12-31",
    }
    for bad in ("0", "0.00", "0.04", "-1"):
        ser = BacktestCreateSerializer(
            data={**base, "max_budget_usd": bad}, context={"request": SimpleNamespace(user=u)},
        )
        assert not ser.is_valid(), bad
        assert "max_budget_usd" in ser.errors, bad
    ok = BacktestCreateSerializer(
        data={**base, "max_budget_usd": "0.05"}, context={"request": SimpleNamespace(user=u)},
    )
    assert ok.is_valid(), ok.errors


@pytest.mark.parametrize("budget,expect_abort", [(Decimal("0"), False), (Decimal("0.01"), True)])
def test_max_budget_zero_disables_the_kill_switch(settings, budget, expect_abort):
    """Unchanged engine semantics, kept as the reason the boundary check above
    exists: a row that already stores 0 still runs uncapped."""
    from apps.backtests.exceptions import BudgetExceeded

    settings.BACKTEST_PRIME_PARALLELISM = 1
    u = User.objects.create_user(email=f"b{budget}@x.test", password="pw-fake-123456789")
    days = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(5)]
    for d in days:
        DailyBar.objects.create(ticker="AAA", date=d, source="fmp", open=1, high=1, low=1,
                                close=1, adjusted_close=1, volume=1)
    bt = Backtest.objects.create(
        user=u, name="budget", universe=["AAA"], start_date=days[0], end_date=days[-1],
        personas=["buffett"], max_budget_usd=budget,
    )
    g = _SpendingGraph(bt.id)
    ctx = [
        patch("apps.graphs.compiler.resolve_graph", return_value=g),
        patch("apps.data.providers.factory.get_fmp_provider", return_value=object()),
        patch("apps.data.providers.factory.get_edgar_provider", return_value=object()),
        patch("apps.data.providers.factory.get_ownership_provider", return_value=object()),
        patch("hedgefund_agents.versioning.ensure_versions_synced"),
        patch("hedgefund_agents.versioning.snapshot_versions", return_value={}),
    ]
    for c in ctx:
        c.start()
    try:
        if expect_abort:
            with pytest.raises(BudgetExceeded):
                prime_agent_cache(bt=bt, start=days[0], end=days[-1], rebalance_freq="daily")
            assert g.calls == 1                      # stopped after the first $50 unit
        else:
            prime_agent_cache(bt=bt, start=days[0], end=days[-1], rebalance_freq="daily")
            assert g.calls == 5                      # ran every unit: $250 spent, cap "0"
    finally:
        for c in ctx:
            c.stop()
