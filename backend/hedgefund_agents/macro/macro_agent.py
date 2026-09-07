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
from ..base import AgentState, pick_model
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

# Version of the deterministic classifier that produced a snapshot.
#   1 = index-LEVEL rules (CPIAUCSL >= 320 -> "high"; INDPRO <= 100 gate on
#       recession). Both are non-stationary index levels, so v1 reads
#       "high inflation" forever after ~2025 and can never say "recession".
#   2 = rates of change: CPI/INDPRO YoY, a 3-month UNRATE change (Sahm-style)
#       and a 6-month fed-funds change. Falls back to v1-style level rules
#       for the axes whose history is unavailable.
CLASSIFIER_VERSION = 2

# Lookback (in months) used to derive each rate of change.
DERIVED_LOOKBACK_MONTHS = {
    "CPIAUCSL": 12,   # YoY inflation
    "INDPRO": 12,     # YoY industrial production
    "UNRATE": 3,      # Sahm-style 3-month change in the unemployment rate
    "FEDFUNDS": 6,    # 6-month change in the policy rate
}

SPEC = AgentSpec(
    agent_name="macro",
    version="v1",
    default_model="openrouter:qwen/qwen3.6-27b",
    prompt="(deterministic classifier + LLM narrative — see macro/macro_agent.py)",
    config={"kind": "analytical"},
)
register(SPEC)


def _pct_change(now: float | None, before: float | None) -> float | None:
    """Percent change between two index levels, or None if not computable."""
    if now is None or before is None or before == 0:
        return None
    return (now / before - 1.0) * 100.0


def _diff(now: float | None, before: float | None) -> float | None:
    if now is None or before is None:
        return None
    return now - before


def classify_regime(
    obs: dict[str, MacroObservation | None],
    prior: dict[str, MacroObservation | None] | None = None,
) -> dict[str, str]:
    """Deterministic regime classification from raw observations.

    ``obs`` holds the latest observation per series as known on ``as_of``.
    ``prior`` holds the SAME series as they stood ``DERIVED_LOOKBACK_MONTHS``
    months earlier (same vintage discipline), which is what makes the
    classification a function of rates of change rather than of index levels:

      * inflation  — CPIAUCSL YoY %, not the CPI index level. CPIAUCSL is
        1982-84=100 and rises forever, so a level rule ("> 320 = high") reads
        high inflation permanently after ~2025 and reads 1980 (13% YoY) as low.
      * growth     — INDPRO YoY % plus a 3-month change in UNRATE (Sahm rule:
        a +0.50pp rise off the recent low is the real-time recession signal).
        INDPRO is 2017=100, so the old ``indpro <= 100`` recession gate became
        structurally unreachable once the index grew past its base year.
      * policy     — 6-month change in FEDFUNDS (are they hiking or cutting),
        not the level.
      * curve      — 2s10s spread (unchanged; a spread is already a rate).

    When ``prior`` is missing for an axis the classifier degrades to the older
    level-based rule for that axis only, so callers that pass a bare ``obs``
    dict (and snapshots stored before ``CLASSIFIER_VERSION`` 2) still work.
    """
    prior = prior or {}
    out = {
        "growth_quadrant": "expansion",
        "inflation_regime": "moderate",
        "yield_curve_state": "normal",
        "policy_stance": "neutral",
    }

    indpro = _as_float(obs.get("INDPRO"))
    unrate = _as_float(obs.get("UNRATE"))
    indpro_yoy = _pct_change(indpro, _as_float(prior.get("INDPRO")))
    unrate_3m = _diff(unrate, _as_float(prior.get("UNRATE")))
    if indpro_yoy is not None or unrate_3m is not None:
        # Rates of change: g = industrial production YoY %, s = 3-month change
        # in the unemployment rate (percentage points).
        g = indpro_yoy if indpro_yoy is not None else 0.0
        s = unrate_3m if unrate_3m is not None else 0.0
        if s >= 0.50 or g <= -2.0:
            out["growth_quadrant"] = "recession"
        elif s >= 0.20 or g < 0.0:
            out["growth_quadrant"] = "slowdown"
        elif s <= -0.20:
            out["growth_quadrant"] = "recovery"
        else:
            out["growth_quadrant"] = "expansion"
    elif unrate is not None:
        # No history: unemployment LEVEL only. The old INDPRO index-level gate
        # is gone — it made "recession" unreachable above the 2017 base.
        if unrate >= 5.5:
            out["growth_quadrant"] = "recession"
        elif unrate >= 4.5:
            out["growth_quadrant"] = "slowdown"
        elif unrate <= 4.0:
            out["growth_quadrant"] = "expansion"
        else:
            out["growth_quadrant"] = "recovery"

    cpi_yoy = _pct_change(_as_float(obs.get("CPIAUCSL")), _as_float(prior.get("CPIAUCSL")))
    if cpi_yoy is not None:
        if cpi_yoy >= 4.0:
            out["inflation_regime"] = "high"
        elif cpi_yoy >= 2.5:
            out["inflation_regime"] = "moderate"
        else:
            out["inflation_regime"] = "low"
    # No year-ago CPI print -> the index level says nothing about inflation;
    # leave the neutral "moderate" default rather than inventing a regime.

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
    fed_6m = _diff(fed, _as_float(prior.get("FEDFUNDS")))
    if fed_6m is not None:
        # Are they hiking or cutting? 2022 at 1.7% was tightening hard; 2008 at
        # 2.0% was easing hard. The level alone cannot tell those apart.
        if fed_6m >= 0.50:
            out["policy_stance"] = "tightening"
        elif fed_6m <= -0.50:
            out["policy_stance"] = "easing"
        else:
            out["policy_stance"] = "neutral"
    elif fed is not None:
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


def _months_before(d, months: int):
    """``d`` shifted back ``months`` calendar months (day clamped to 28)."""
    import datetime as _dt

    total = (d.year * 12 + (d.month - 1)) - months
    return _dt.date(total // 12, total % 12 + 1, min(d.day, 28))


def _fetch_prior_observations(
    as_of, obs: dict[str, MacroObservation | None]
) -> dict[str, MacroObservation | None]:
    """The lagged reading of each derived series, straight from the local
    ``MacroSeries`` cache that ``get_latest_value`` just backfilled.

    DB-only: no extra ALFRED calls. Vintage discipline is preserved
    (``vintage_date <= as_of``), so this stays point-in-time safe.
    """
    from apps.data.models import MacroSeries

    prior: dict[str, MacroObservation | None] = {}
    for sid, months in DERIVED_LOOKBACK_MONTHS.items():
        latest = obs.get(sid)
        if latest is None:
            prior[sid] = None
            continue
        target = _months_before(latest.date, months)
        row = (
            MacroSeries.objects.filter(
                series_id=sid, vintage_date__lte=as_of, date__lte=target, value__isnull=False
            )
            .order_by("-date", "-vintage_date")
            .first()
        )
        prior[sid] = _to_obs_or_none(row)
    return prior


def _to_obs_or_none(row) -> MacroObservation | None:
    if row is None:
        return None
    return MacroObservation(
        series_id=row.series_id, date=row.date, vintage_date=row.vintage_date, value=row.value
    )


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
        # FRED is public-data (in `_PUBLIC`), so the factory naturally falls
        # back to env-key when no user key is set. Honors a user's BYOK FRED
        # key first when present.
        provider = get_fred_provider(user=user_id)
    obs = _fetch_observations(provider, as_of)
    prior = _fetch_prior_observations(as_of, obs)
    regime = classify_regime(obs, prior)
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
            # Stamped so rows written by the index-LEVEL classifier (v1) stay
            # readable and identifiable after the rules changed.
            "classifier_version": CLASSIFIER_VERSION,
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
    provider, model = pick_model(state or {}, "macro", default)
    client = get_llm(provider, state=state)
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


def _macro_freshness(snapshot, as_of) -> dict:
    """P02b review: explicit freshness/provider/source state on macro output.

    ``stale`` is True when the consumed snapshot is more than 14 days
    older than ``as_of`` — long enough that the regime classifier may
    have moved on. ``fallback`` is True when ``series_used`` contains
    only None values, signalling the provider call failed and we are
    rendering an unpopulated snapshot.
    """
    import datetime as _dt

    age_days = (as_of - snapshot.as_of_date).days
    series_used = getattr(snapshot, "series_used", None) or {}
    has_any_value = any(v is not None for v in series_used.values())
    created_at = getattr(snapshot, "created_at", None)
    return {
        "snapshot_date": snapshot.as_of_date.isoformat(),
        "snapshot_age_days": int(age_days),
        "retrieved_at": (
            created_at.isoformat()
            if isinstance(created_at, _dt.datetime)
            else None
        ),
        "provider": "fred",
        "stale": age_days > 14,
        "partial": not has_any_value,
        "fallback": not has_any_value,
        "series_used": list(series_used.keys()),
    }


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
    payload = out.model_dump()
    # P02b review: append provenance/freshness metadata that the run-detail
    # UI consumes. Kept as a sibling key rather than added to MacroOutput so
    # the LLM contract stays narrow.
    payload["_freshness"] = _macro_freshness(snapshot, as_of)
    # P7: cross-asset trend (TSMOM) for THIS candidate — a regime input the
    # council debates (the CTA-lite sleeve, Account 3). Additive + best-effort:
    # the council runs per-ticker, so each candidate gets its own trend posture.
    try:
        from .tsmom import tsmom_score

        ticker = state.get("ticker") if state else None
        provider = state.get("data_provider") if state else None
        if ticker and provider is not None:
            ts = tsmom_score(ticker, as_of, provider)
            payload["tsmom"] = ts
            if ts.get("available"):
                payload["narrative"] = (
                    f"{payload.get('narrative', '')}\n\nCross-asset trend (TSMOM) "
                    f"for {ticker}: {ts['posture']} (1/3/12-mo blend "
                    f"{ts['score']:+.1%})."
                ).strip()
    except Exception:  # noqa: BLE001 — trend is an optional regime input
        pass
    return {"macro": payload}  # type: ignore[return-value]


# Silence unused warning on Decimal import — kept for tests/typing.
_ = Decimal
