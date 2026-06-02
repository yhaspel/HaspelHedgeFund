"""P4c: transient per-cycle model/tier override from the `Run cycle now`
dispatch modal.

The modal lets the user switch price tier and/or pick individual models for
this dispatch only — nothing is persisted to the strategy or to the user's
global model preferences. These tests cover:
  - estimate_cycle / _resolve_model_overrides honouring the transient choice,
  - the estimate endpoint re-estimating via POST (preset + model_overrides),
  - validation (unknown preset / agent / model → 400),
  - run-now threading the choice into daily_long_short_cycle.delay,
  - the untouched (no-body) path staying byte-identical to before.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.models_catalog.models import ModelEntry
from apps.portfolios.models import Portfolio, PortfolioStrategy, Universe
from apps.portfolios.tasks import _resolve_model_overrides, estimate_cycle

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="cdo@example.com", password="supersecret")


def _client(email: str) -> APIClient:
    c = APIClient()
    token = c.post(
        reverse("login"), {"email": email, "password": "supersecret"}, format="json"
    ).data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def _strategy(user, **kwargs) -> PortfolioStrategy:
    universe = Universe.objects.create(name="cdo-uni", is_active=True)
    portfolio = Portfolio.objects.create(
        user=user, name="strat", kind="strategy", cash_balance=Decimal("100000"),
    )
    return PortfolioStrategy.objects.create(
        user=user, name="s", universe=universe, portfolio=portfolio, **kwargs,
    )


def _seed_model(model_id: str = "openrouter:acme/frugal-test") -> str:
    ModelEntry.objects.create(
        id=model_id, provider="openrouter", display_name="Acme Frugal Test",
        tier="hosted_open", price_in_per_mtok=Decimal("0.5"),
        price_out_per_mtok=Decimal("1.0"), is_active=True,
    )
    return model_id


# ---- estimate_cycle / _resolve_model_overrides (unit) --------------------

@pytest.mark.django_db
def test_estimate_cycle_override_preset_expands_that_tier(user) -> None:
    strategy = _strategy(user, model_preset="research")
    est = estimate_cycle(strategy, override_preset="dev")
    # Echoes the chosen tier, not the strategy's saved preset.
    assert est["preset"] == "dev"
    # dev maps every persona onto a distinct OpenRouter :free slug.
    assert est["overrides"]["buffett"] == "openrouter:openai/gpt-oss-120b:free"


@pytest.mark.django_db
def test_estimate_cycle_override_models_used_verbatim(user) -> None:
    strategy = _strategy(user, model_preset="research")
    chosen = {"buffett": "openrouter:acme/x", "munger": "openrouter:acme/y"}
    est = estimate_cycle(strategy, override_models=chosen)
    assert est["overrides"] == chosen
    # preset echoes the strategy's saved tier when only models were overridden.
    assert est["preset"] == "research"


@pytest.mark.django_db
def test_resolve_model_overrides_explicit_overrides_win(user) -> None:
    strategy = _strategy(user, model_preset="research")
    out = _resolve_model_overrides(
        strategy, preset="dev", overrides={"buffett": "openrouter:acme/x"}
    )
    # An explicit map wins outright — preset is ignored.
    assert out == {"buffett": "openrouter:acme/x"}


@pytest.mark.django_db
def test_resolve_model_overrides_explicit_preset_bypasses_strategy(user) -> None:
    strategy = _strategy(user, model_preset="research")
    out = _resolve_model_overrides(strategy, preset="dev")
    assert out["buffett"] == "openrouter:openai/gpt-oss-120b:free"


@pytest.mark.django_db
def test_resolve_model_overrides_no_args_unchanged(user) -> None:
    # Regression guard: the no-kwarg path still resolves from the strategy.
    strategy = _strategy(user, model_preset="frugal")
    out = _resolve_model_overrides(strategy)
    assert out["lynch"] == "openrouter:qwen/qwen3.6-27b"


# ---- estimate endpoint (POST) --------------------------------------------

@pytest.mark.django_db
def test_estimate_post_with_preset(user) -> None:
    strategy = _strategy(user, model_preset="research")
    client = _client("cdo@example.com")
    resp = client.post(
        reverse("strategy-estimate", args=[strategy.pk]),
        {"preset": "dev"}, format="json",
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["preset"] == "dev"
    assert resp.data["overrides"]["buffett"] == "openrouter:openai/gpt-oss-120b:free"


@pytest.mark.django_db
def test_estimate_post_unknown_preset_400(user) -> None:
    strategy = _strategy(user)
    client = _client("cdo@example.com")
    resp = client.post(
        reverse("strategy-estimate", args=[strategy.pk]),
        {"preset": "platinum"}, format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_estimate_post_unknown_agent_key_400(user) -> None:
    strategy = _strategy(user)
    client = _client("cdo@example.com")
    resp = client.post(
        reverse("strategy-estimate", args=[strategy.pk]),
        {"model_overrides": {"fundamentls": "openrouter:acme/x"}}, format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_estimate_post_unknown_model_400(user) -> None:
    strategy = _strategy(user)
    client = _client("cdo@example.com")
    resp = client.post(
        reverse("strategy-estimate", args=[strategy.pk]),
        {"model_overrides": {"buffett": "openrouter:does/not-exist"}},
        format="json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_estimate_post_valid_model_ok(user) -> None:
    strategy = _strategy(user, top_k_longs=2, top_k_shorts=1)
    mid = _seed_model()
    client = _client("cdo@example.com")
    resp = client.post(
        reverse("strategy-estimate", args=[strategy.pk]),
        {"model_overrides": {"buffett": mid}}, format="json",
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["overrides"]["buffett"] == mid
    buffett_row = next(r for r in resp.data["per_agent"] if r["agent"] == "buffett")
    assert buffett_row["model"] == mid
    assert buffett_row["per_call_usd"] > 0  # priced from the seeded ModelEntry


# ---- run-now endpoint ----------------------------------------------------

@pytest.mark.django_db
def test_run_now_threads_override_preset(user) -> None:
    strategy = _strategy(user)
    client = _client("cdo@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        mock_task.return_value.id = "t1"
        resp = client.post(
            reverse("strategy-run-now", args=[strategy.pk]),
            {"as_of_date": "2026-05-29", "preset": "dev"}, format="json",
        )
    assert resp.status_code == 202, resp.data
    mock_task.assert_called_once_with(
        strategy.pk, "2026-05-29", force=False,
        override_preset="dev", override_models=None,
    )


@pytest.mark.django_db
def test_run_now_threads_model_overrides(user) -> None:
    strategy = _strategy(user)
    mid = _seed_model()
    client = _client("cdo@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        mock_task.return_value.id = "t2"
        resp = client.post(
            reverse("strategy-run-now", args=[strategy.pk]),
            {"as_of_date": "2026-05-29", "model_overrides": {"buffett": mid}},
            format="json",
        )
    assert resp.status_code == 202, resp.data
    mock_task.assert_called_once_with(
        strategy.pk, "2026-05-29", force=False,
        override_preset=None, override_models={"buffett": mid},
    )


@pytest.mark.django_db
def test_run_now_no_body_backward_compatible(user) -> None:
    strategy = _strategy(user)
    client = _client("cdo@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        mock_task.return_value.id = "t3"
        resp = client.post(
            reverse("strategy-run-now", args=[strategy.pk]),
            {"as_of_date": "2026-05-29"}, format="json",
        )
    assert resp.status_code == 202, resp.data
    mock_task.assert_called_once_with(
        strategy.pk, "2026-05-29", force=False,
        override_preset=None, override_models=None,
    )


# ---- budget trim honours the transient override (faithfulness) -----------

def _seed_cheap_and_expensive() -> tuple[str, str]:
    ModelEntry.objects.create(
        id="openrouter:acme/cheap", provider="openrouter", display_name="Cheap",
        tier="hosted_open", price_in_per_mtok=Decimal("0.05"),
        price_out_per_mtok=Decimal("0.05"), is_active=True,
    )
    ModelEntry.objects.create(
        id="anthropic:acme/expensive", provider="anthropic", display_name="Pricey",
        tier="frontier", price_in_per_mtok=Decimal("15.0"),
        price_out_per_mtok=Decimal("75.0"), is_active=True,
    )
    return "openrouter:acme/cheap", "anthropic:acme/expensive"


@pytest.mark.django_db
def test_per_call_cost_reflects_transient_override(user) -> None:
    from apps.portfolios.tasks import PER_AGENT_TOKEN_ESTIMATES, _per_council_call_cost

    strategy = _strategy(user, model_preset="dev")
    cheap, exp = _seed_cheap_and_expensive()
    agents = [a for a in PER_AGENT_TOKEN_ESTIMATES if a != "cio"]
    lo = _per_council_call_cost(strategy, overrides={a: cheap for a in agents})
    hi = _per_council_call_cost(strategy, overrides={a: exp for a in agents})
    assert hi > lo > 0


@pytest.mark.django_db
def test_budget_trim_honours_transient_override(user) -> None:
    """Regression: the actual dispatch trim must price the SAME models the
    estimate showed, not the strategy's saved preset."""
    from apps.portfolios.tasks import PER_AGENT_TOKEN_ESTIMATES, _trim_k_for_budget

    strategy = _strategy(
        user, model_preset="dev", cost_ceiling_per_cycle_usd=Decimal("1.00"),
    )
    cheap, exp = _seed_cheap_and_expensive()
    agents = [a for a in PER_AGENT_TOKEN_ESTIMATES if a != "cio"]
    cheap_l, cheap_s = _trim_k_for_budget(
        strategy, 5, 5, overrides={a: cheap for a in agents}
    )
    exp_l, exp_s = _trim_k_for_budget(
        strategy, 5, 5, overrides={a: exp for a in agents}
    )
    # The pricey override fits fewer candidates under the same ceiling.
    assert (cheap_l + cheap_s) > (exp_l + exp_s)


@pytest.mark.django_db
def test_run_now_rejects_unknown_model(user) -> None:
    strategy = _strategy(user)
    client = _client("cdo@example.com")
    with patch("apps.portfolios.views.daily_long_short_cycle.delay") as mock_task:
        resp = client.post(
            reverse("strategy-run-now", args=[strategy.pk]),
            {"model_overrides": {"buffett": "openrouter:nope/missing"}},
            format="json",
        )
    assert resp.status_code == 400
    mock_task.assert_not_called()
