"""Macro agent.

Pulls a fixed set of FRED series, classifies the regime DETERMINISTICALLY
in Python (so backtests are reproducible), then asks an LLM only for the
narrative + sector tilts. Snapshots are cached per as_of_date.
"""
from __future__ import annotations

import json
from decimal import Decimal

from apps.data.models import MacroSnapshot
from apps.data.providers.factory import get_fred_provider
from apps.data.providers.fred import FredProvider, MacroObservation

from .._persist import record_llm_call
from ..base import AgentState
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import MacroOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AgentSpec, register

MACRO_SERIES = [
    "GDPC1",     # real GDP
    "CPIAUCSL",  # CPI
    "UNRATE",    # unemployment
    "DGS10",     # 10y yield
    "DGS2",      # 2y yield
    "FEDFUNDS",  # fed funds
    "T10Y2Y",    # 10y-2y spread
    "INDPRO",    # industrial production
]

SPEC = AgentSpec(
    agent_name="macro",
    version="v1",
    default_model="openrouter:qwen/qwen3.6-27b",
    prompt="(deterministic classifier + LLM narrative — see macro/macro_agent.py)",
    config={"kind": "analytical"},
)
register(SPEC)


def classify_regime(obs: dict[str, MacroObservation | None]) -> dict[str, str]:
    """Deterministic regime classification from raw observations.

    Returns a dict with keys: growth_quadrant, inflation_regime,
    yield_curve_state, policy_stance.
    """
    out = {
        "growth_quadrant": "expansion",
        "inflation_regime": "moderate",
        "yield_curve_state": "normal",
        "policy_stance": "neutral",
    }

    indpro = _as_float(obs.get("INDPRO"))
    unrate = _as_float(obs.get("UNRATE"))
    if indpro is not None and unrate is not None:
        if unrate >= 5.5 and (indpro or 0) <= 100:
            out["growth_quadrant"] = "recession"
        elif unrate >= 4.5:
            out["growth_quadrant"] = "slowdown"
        elif unrate <= 4.0 and indpro >= 100:
            out["growth_quadrant"] = "expansion"
        else:
            out["growth_quadrant"] = "recovery"

    cpi = _as_float(obs.get("CPIAUCSL"))
    if cpi is not None:
        if cpi >= 320:
            out["inflation_regime"] = "high"
        elif cpi >= 290:
            out["inflation_regime"] = "moderate"
        else:
            out["inflation_regime"] = "low"

    dgs10 = _as_float(obs.get("DGS10"))
    dgs2 = _as_float(obs.get("DGS2"))
    spread = _as_float(obs.get("T10Y2Y"))
    if spread is None and dgs10 is not None and dgs2 is not None:
        spread = dgs10 - dgs2
    if spread is not None:
        if spread < 0:
            out["yield_curve_state"] = "inverted"
        elif spread < 0.5:
            out["yield_curve_state"] = "flat"
        else:
            out["yield_curve_state"] = "normal"

    fed = _as_float(obs.get("FEDFUNDS"))
    if fed is not None:
        if fed >= 4.5:
            out["policy_stance"] = "tightening"
        elif fed <= 2.0:
            out["policy_stance"] = "easing"
        else:
            out["policy_stance"] = "neutral"

    return out


def _as_float(o: MacroObservation | None) -> float | None:
    if o is None or o.value is None:
        return None
    return float(o.value)


def _fetch_observations(
    provider: FredProvider, as_of
) -> dict[str, MacroObservation | None]:
    return {sid: provider.get_latest_value(sid, as_of=as_of) for sid in MACRO_SERIES}


def compute_snapshot(
    as_of,
    provider: FredProvider | None = None,
    *,
    run_id: int | None = None,
    backtest_id: int | None = None,
    state: dict | None = None,
) -> MacroSnapshot:
    """Build (or fetch from cache) the MacroSnapshot for `as_of`.

    `run_id` / `backtest_id` attribute the LLM-narrative call to the caller.
    The Celery beat pre-warm task passes neither (the resulting LLMCall is
    intentionally an unattributed "shared prewarm" — visible in the cost
    dashboard, not billed to any single run).
    """
    cached = MacroSnapshot.objects.filter(as_of_date=as_of).first()
    if cached:
        return cached

    if provider is None:
        user_id = (state or {}).get("user_id") if state else None
        # FRED is public-data — force_platform=True keeps the prewarm path
        # working in prod without weakening the gate for paid providers.
        provider = get_fred_provider(user=user_id, force_platform=True)
    obs = _fetch_observations(provider, as_of)
    regime = classify_regime(obs)
    series_used = {
        sid: float(o.value) if (o and o.value is not None) else None
        for sid, o in obs.items()
    }

    # P2m: pull the deterministic Markov consensus (cheap read; never refits).
    # Empty when prewarm hasn't run yet; the LLM sees that explicitly.
    from .regime_persistence import compute_markov_consensus
    markov_consensus = compute_markov_consensus(as_of_date=as_of)

    narrative, sector_tilts = _llm_narrative(
        as_of=as_of,
        regime=regime,
        series_used=series_used,
        markov_consensus=markov_consensus,
        state=state,
        run_id=run_id,
        backtest_id=backtest_id,
    )

    snapshot, _ = MacroSnapshot.objects.update_or_create(
        as_of_date=as_of,
        defaults={
            **regime,
            "narrative": narrative,
            "sector_implications": sector_tilts,
            "series_used": series_used,
            "markov_consensus": markov_consensus,
        },
    )
    return snapshot


def _llm_narrative(
    *, as_of, regime: dict[str, str], series_used: dict[str, float | None],
    markov_consensus: dict | None = None,
    state: dict | None = None,
    run_id: int | None = None,
    backtest_id: int | None = None,
):
    default = DEFAULT_MODELS.get("macro", ("openrouter", "qwen/qwen3.6-27b"))
    provider, model = default
    client = get_llm(provider)
    system = (
        "You are a macro strategist. The growth/inflation/yield-curve/policy "
        "regime is ALREADY classified deterministically — do not re-classify. "
        "Write a 3-5 sentence narrative tying the four states together, then "
        "suggest sector tilts. A deterministic Markov regime consensus across "
        "16 broad-market ETFs is included as price-action context: treat it "
        "as a cheap cross-check on your macro thesis (agreement reinforces; "
        "disagreement is itself a signal, not a thing to argue with). "
        "Return MacroOutput JSON."
    )
    markov_block = (
        json.dumps(markov_consensus, indent=2)
        if markov_consensus
        else "(no Markov consensus available — prewarm has not run for this date)"
    )
    user = (
        f"As-of date: {as_of.isoformat()}\n"
        f"CLASSIFIED REGIME:\n{json.dumps(regime, indent=2)}\n\n"
        f"RAW SERIES (latest as known on as_of):\n{json.dumps(series_used, indent=2)}\n\n"
        f"MARKOV REGIME CONSENSUS (price-action, deterministic):\n{markov_block}\n\n"
        "Suggest sector_implications for at least: technology, financials, "
        "energy, healthcare, consumer_staples, consumer_discretionary, "
        "utilities, industrials."
    )
    from apps.backtests.cache import make_cache_ctx
    parsed, resp = call_structured(
        client,
        model=model,
        schema=MacroOutput,
        messages=[Message("system", system), Message("user", user)],
        max_tokens=4096,
        temperature=0.3,
        cache_ctx=make_cache_ctx(state or {}, "macro"),
    )
    record_llm_call(
        run_id=run_id,
        backtest_id=backtest_id,
        agent_name="macro",
        resp=resp,
    )
    return parsed.narrative, parsed.sector_implications


def run_macro(state: AgentState) -> AgentState:
    as_of = state["as_of_date"]
    snapshot = compute_snapshot(
        as_of,
        run_id=state.get("run_id"),
        backtest_id=state.get("backtest_id"),
        state=state,
    )
    out = MacroOutput(
        as_of_date=snapshot.as_of_date.isoformat(),
        growth_quadrant=snapshot.growth_quadrant,  # type: ignore[arg-type]
        inflation_regime=snapshot.inflation_regime,  # type: ignore[arg-type]
        yield_curve_state=snapshot.yield_curve_state,  # type: ignore[arg-type]
        policy_stance=snapshot.policy_stance,  # type: ignore[arg-type]
        narrative=snapshot.narrative,
        sector_implications=snapshot.sector_implications or {},
    )
    return {"macro": out.model_dump()}  # type: ignore[return-value]


# Silence unused warning on Decimal import — kept for tests/typing.
_ = Decimal
