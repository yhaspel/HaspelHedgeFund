"""Persistence + integration glue for the Markov regime classifier (P2m).

This module is the integration layer between the deterministic fitter in
``markov_regime`` and the rest of the project. It owns:

* ``ALWAYS_MODELLED_TICKERS`` — the 16-ticker universe the daily Celery
  prewarm task fits unconditionally (SPY, QQQ, the 11 SPDR sector ETFs,
  TLT, GLD, UUP).
* ``fit_and_persist`` — fits a model for one ticker at one as_of_date and
  saves ``RegimeModel`` + ``RegimeSnapshot`` rows. Idempotent on
  ``(ticker, as_of_date, model_type, config_hash)``.
* ``get_latest_snapshot`` / ``get_batch_snapshots`` — read helpers used by
  the views and downstream strategies.
* ``compute_markov_consensus`` — vote-aggregation across the
  always-modelled universe for ``MacroSnapshot.markov_consensus``.

The fit path uses the factory-supplied FMP provider so backtest replay
providers stay honest. Callers must pass an ``as_of_date`` - there's no
implicit "today" magic. The prewarm Celery task passes ``force_platform=True``
because it fits the always-modelled universe ahead of any user-triggered
access (see data-licensing.md trade-off in P2n risk #3).
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.data.models import RegimeModel, RegimeSnapshot

from .markov_regime import (
    InsufficientHistoryError,
    MarkovConfig,
    RegimeFit,
    UndertrainedStateError,
    fit_regime,
)

log = logging.getLogger(__name__)


# Always-modelled universe: the 16 tickers the prewarm task fits daily so
# the dashboard / strategy widgets / Markov consensus never wait on a cold
# fetch. SPY + QQQ for broad equity, 11 SPDR sectors for sector breadth, and
# TLT/GLD/UUP for the rates / commodity / FX dimensions.
ALWAYS_MODELLED_TICKERS: tuple[str, ...] = (
    "SPY", "QQQ",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "TLT", "GLD", "UUP",
)

# Snapshots older than this are considered stale (matrix-power forecast
# collapses to the stationary distribution well before 28 days anyway).
DEFAULT_STALENESS_DAYS = 10


# ---------------------------------------------------------------------------
# Fitter glue
# ---------------------------------------------------------------------------

def _fetch_bars(
    ticker: str, *, as_of_date: dt.date, lookback_observations: int, window_days: int,
    data_provider: Any | None = None,
) -> list[Any]:
    """Pull daily bars before ``as_of_date`` using the registered provider.

    Uses calendar-day padding ``~ (lookback + window) * 1.6`` to be robust
    to weekends/holidays. Returns the raw Bar dataclasses unchanged.
    """
    if data_provider is None:
        from apps.data.providers.factory import get_fmp_provider

        # No user context inside the regime fitter — callers (prewarm task,
        # portfolio cycles) own the user-keyed provider and inject it.
        # Default falls back to a platform-keyed provider when policy allows
        # (dev/test, or prod via force_platform from prewarm).
        data_provider = get_fmp_provider(force_platform=True)
    days_needed = int((lookback_observations + window_days) * 1.6) + 30
    start = as_of_date - dt.timedelta(days=days_needed)
    end = as_of_date - dt.timedelta(days=1)
    if end < start:
        end = start
    try:
        bars = data_provider.get_daily_bars(ticker, start=start, end=end, as_of=as_of_date)
    except Exception as exc:  # pragma: no cover - provider transient errors
        log.warning("regime: bar fetch failed for %s: %s", ticker, exc)
        return []
    return list(bars)


def _decimal(value: float) -> Decimal:
    return Decimal(f"{float(value):.4f}")


def _prior_snapshot(
    ticker: str, as_of_date: dt.date, model_type: str, config_hash: str
) -> RegimeSnapshot | None:
    return (
        RegimeSnapshot.objects.filter(
            ticker=ticker,
            model_type=model_type,
            config_hash=config_hash,
            as_of_date__lt=as_of_date,
        )
        .order_by("-as_of_date")
        .first()
    )


@transaction.atomic
def _persist(fit: RegimeFit, *, as_of_date: dt.date) -> RegimeSnapshot:
    """Upsert RegimeModel + RegimeSnapshot rows from a successful fit."""
    cfg = fit.config
    config_hash = cfg.hash()

    rm, _ = RegimeModel.objects.update_or_create(
        ticker=fit.ticker,
        as_of_date=as_of_date,
        model_type=cfg.model_type,
        config_hash=config_hash,
        defaults={
            "return_window_days": int(cfg.return_window_days),
            "bull_threshold_return": _decimal(cfg.bull_threshold),
            "bear_threshold_return": _decimal(cfg.bear_threshold),
            "price_field": cfg.price_field,
            "transition_matrix": fit.transition_matrix,
            "state_means": fit.state_means,
            "state_stds": fit.state_stds,
            "state_labels": fit.state_labels,
            "stationary_distribution": fit.stationary_distribution,
            "fit_observations": int(fit.fit_observations),
            "observations_available": int(fit.observations_available),
            "fit_lookback_observations": int(cfg.fit_lookback_observations),
            "training_start_date": fit.training_start_date,
            "training_end_date": fit.training_end_date,
            "log_likelihood": fit.log_likelihood,
            "fit_metadata": {**fit.fit_metadata, "state_counts": fit.state_counts},
        },
    )

    forecast_1d = fit.forecast(1)
    forecast_5d = fit.forecast(5)
    prior = _prior_snapshot(fit.ticker, as_of_date, cfg.model_type, config_hash)
    state_changed = bool(prior and prior.current_state != fit.current_state)
    cur_persist_delta = (
        fit.current_state_persistence - prior.current_state_persistence
        if prior is not None
        else None
    )
    bull_delta = (
        fit.bull_persistence - prior.bull_persistence if prior is not None else None
    )
    bear_delta = (
        fit.bear_persistence - prior.bear_persistence if prior is not None else None
    )

    snap, _ = RegimeSnapshot.objects.update_or_create(
        ticker=fit.ticker,
        as_of_date=as_of_date,
        model_type=cfg.model_type,
        config_hash=config_hash,
        defaults={
            "source_model": rm,
            "last_price_date": fit.last_price_date,
            "current_state": fit.current_state,
            "current_return": fit.current_return,
            "current_state_persistence": fit.current_state_persistence,
            "bull_persistence": fit.bull_persistence,
            "sideways_persistence": fit.sideways_persistence,
            "bear_persistence": fit.bear_persistence,
            "bull_prob_1d": forecast_1d["bull"],
            "sideways_prob_1d": forecast_1d["sideways"],
            "bear_prob_1d": forecast_1d["bear"],
            "bull_prob_5d": forecast_5d["bull"],
            "sideways_prob_5d": forecast_5d["sideways"],
            "bear_prob_5d": forecast_5d["bear"],
            "bull_minus_bear_1d": forecast_1d["bull"] - forecast_1d["bear"],
            "prior_snapshot": prior,
            "prior_current_state": (prior.current_state if prior else ""),
            "current_state_persistence_delta": cur_persist_delta,
            "bull_persistence_delta": bull_delta,
            "bear_persistence_delta": bear_delta,
            "state_changed_from_prior": state_changed,
            "stale": False,
        },
    )
    return snap


def fit_and_persist(
    *,
    ticker: str,
    as_of_date: dt.date,
    config: MarkovConfig | None = None,
    data_provider: Any | None = None,
) -> RegimeSnapshot:
    """Fit + persist for one (ticker, as_of_date). Returns the snapshot row.

    Raises InsufficientHistoryError / UndertrainedStateError if the fit is
    refused by guardrails. Callers in the prewarm task catch these and log
    the ticker as a "missing" entry instead of crashing the cycle.
    """
    cfg = config or MarkovConfig()
    bars = _fetch_bars(
        ticker,
        as_of_date=as_of_date,
        lookback_observations=cfg.fit_lookback_observations,
        window_days=cfg.return_window_days,
        data_provider=data_provider,
    )
    fit = fit_regime(ticker=ticker, as_of_date=as_of_date, bars=bars, config=cfg)
    return _persist(fit, as_of_date=as_of_date)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_latest_snapshot(
    ticker: str,
    *,
    as_of_date: dt.date,
    model_type: str = "labelled_markov",
    config: MarkovConfig | None = None,
    staleness_days: int = DEFAULT_STALENESS_DAYS,
) -> RegimeSnapshot | None:
    """Read the latest persisted snapshot ``as_of_date <= given``.

    Mutates ``stale`` to True (in memory) when the snapshot's
    ``as_of_date`` is older than ``staleness_days`` so callers can decide
    whether to use it or fall back. Never writes; never re-fits.
    """
    cfg = config or MarkovConfig(model_type=model_type)
    config_hash = cfg.hash()
    snap = (
        RegimeSnapshot.objects.filter(
            ticker=ticker,
            model_type=model_type,
            config_hash=config_hash,
            as_of_date__lte=as_of_date,
        )
        .select_related("source_model")
        .order_by("-as_of_date")
        .first()
    )
    if snap is None:
        return None
    age = (as_of_date - snap.as_of_date).days
    snap.stale = age > staleness_days
    return snap


def get_batch_snapshots(
    tickers: Iterable[str],
    *,
    as_of_date: dt.date,
    model_type: str = "labelled_markov",
    config: MarkovConfig | None = None,
) -> dict[str, RegimeSnapshot | None]:
    return {t.upper(): get_latest_snapshot(
        t.upper(), as_of_date=as_of_date, model_type=model_type, config=config
    ) for t in tickers}


def snapshot_to_dict(snap: RegimeSnapshot | None) -> dict | None:
    if snap is None:
        return None
    return {
        "ticker": snap.ticker,
        "as_of_date": snap.as_of_date.isoformat(),
        "model_type": snap.model_type,
        "config_hash": snap.config_hash,
        "last_price_date": snap.last_price_date.isoformat(),
        "current_state": snap.current_state,
        "current_return": snap.current_return,
        "current_state_persistence": snap.current_state_persistence,
        "bull_persistence": snap.bull_persistence,
        "sideways_persistence": snap.sideways_persistence,
        "bear_persistence": snap.bear_persistence,
        "bull_prob_1d": snap.bull_prob_1d,
        "sideways_prob_1d": snap.sideways_prob_1d,
        "bear_prob_1d": snap.bear_prob_1d,
        "bull_prob_5d": snap.bull_prob_5d,
        "sideways_prob_5d": snap.sideways_prob_5d,
        "bear_prob_5d": snap.bear_prob_5d,
        "bull_minus_bear_1d": snap.bull_minus_bear_1d,
        "prior_current_state": snap.prior_current_state,
        "current_state_persistence_delta": snap.current_state_persistence_delta,
        "bull_persistence_delta": snap.bull_persistence_delta,
        "bear_persistence_delta": snap.bear_persistence_delta,
        "state_changed_from_prior": snap.state_changed_from_prior,
        "stale": snap.stale,
    }


# ---------------------------------------------------------------------------
# Consensus aggregation for MacroSnapshot.markov_consensus
# ---------------------------------------------------------------------------

def compute_markov_consensus(
    *,
    as_of_date: dt.date,
    tickers: Iterable[str] = ALWAYS_MODELLED_TICKERS,
    model_type: str = "labelled_markov",
    config: MarkovConfig | None = None,
    staleness_days: int = DEFAULT_STALENESS_DAYS,
) -> dict:
    """Aggregate fresh Markov snapshots into a single consensus dict.

    Shape:

        {
            "per_ticker": {"SPY": {state, bull_minus_bear_1d, persistence}, ...},
            "vote": {"bull": ..., "sideways": ..., "bear": ...},
            "consensus_state": "bull",
            "consensus_strength": 0.5,
            "available_count": N, "stale_count": M,
            "missing": [{"ticker": "UUP", "reason": "undertrained_state"}, ...],
        }
    """
    per_ticker: dict[str, dict] = {}
    vote = {"bull": 0, "sideways": 0, "bear": 0}
    missing: list[dict] = []
    stale_count = 0
    available_count = 0

    for t in tickers:
        snap = get_latest_snapshot(
            t.upper(),
            as_of_date=as_of_date,
            model_type=model_type,
            config=config,
            staleness_days=staleness_days,
        )
        if snap is None:
            missing.append({"ticker": t.upper(), "reason": "no_snapshot"})
            continue
        per_ticker[t.upper()] = {
            "state": snap.current_state,
            "bull_minus_bear_1d": round(float(snap.bull_minus_bear_1d), 4),
            "persistence": round(float(snap.current_state_persistence), 4),
            "as_of_date": snap.as_of_date.isoformat(),
            "stale": bool(snap.stale),
        }
        if snap.stale:
            stale_count += 1
            continue
        vote[snap.current_state] = vote.get(snap.current_state, 0) + 1
        available_count += 1

    if available_count > 0:
        consensus_state = max(vote.items(), key=lambda kv: kv[1])[0]
        consensus_strength = vote[consensus_state] / available_count
    else:
        consensus_state = "unavailable"
        consensus_strength = 0.0

    return {
        "per_ticker": per_ticker,
        "vote": vote,
        "consensus_state": consensus_state,
        "consensus_strength": round(consensus_strength, 4),
        "available_count": available_count,
        "stale_count": stale_count,
        "missing": missing,
    }


# Deterministic mapping between macro-agent risk bucket and Markov bucket.
# bull → risk_on; bear → risk_off; sideways → neutral.
MARKOV_TO_RISK_BUCKET = {
    "bull": "risk_on",
    "sideways": "neutral",
    "bear": "risk_off",
    "unavailable": "neutral",
}


def macro_risk_bucket(macro_snapshot: Any) -> str:
    """Deterministic risk bucket from MacroSnapshot fields.

    risk_off if growth_quadrant == 'recession' OR (yield_curve_state ==
    'inverted' AND policy_stance == 'tightening'); risk_on if
    growth_quadrant in {'expansion', 'recovery'} AND yield_curve_state !=
    'inverted'; otherwise 'neutral'.
    """
    growth = (getattr(macro_snapshot, "growth_quadrant", "") or "").lower()
    curve = (getattr(macro_snapshot, "yield_curve_state", "") or "").lower()
    policy = (getattr(macro_snapshot, "policy_stance", "") or "").lower()
    if growth == "recession" or (curve == "inverted" and policy == "tightening"):
        return "risk_off"
    if growth in {"expansion", "recovery"} and curve != "inverted":
        return "risk_on"
    return "neutral"


def disagreement_with_markov(
    macro_snapshot: Any, consensus: dict | None
) -> tuple[bool, str]:
    """Return (disagrees, reason).

    Disagrees when the deterministic macro risk bucket and the Markov
    consensus risk bucket are opposite ends (risk_on vs risk_off). Neutral
    on either side is not disagreement — it's "no strong signal".
    """
    if not consensus or consensus.get("consensus_state") in (None, "unavailable"):
        return False, ""
    macro_bucket = macro_risk_bucket(macro_snapshot)
    markov_state = consensus["consensus_state"]
    markov_bucket = MARKOV_TO_RISK_BUCKET.get(markov_state, "neutral")
    if {macro_bucket, markov_bucket} == {"risk_on", "risk_off"}:
        return True, (
            f"Macro risk={macro_bucket} (growth={getattr(macro_snapshot, 'growth_quadrant', '')}"
            f" curve={getattr(macro_snapshot, 'yield_curve_state', '')})"
            f" but Markov consensus={markov_state}"
            f" ({consensus.get('vote')})."
        )
    return False, ""


def _ignore_dataclass(obj: RegimeFit) -> dict:  # pragma: no cover - shim
    """Kept around so future code can serialize a RegimeFit without pickling it."""
    return asdict(obj)
