"""Apply the Markov regime exposure scaler to a long-short cycle's constraints (P2m).

The scaler is opt-in per ``PortfolioStrategy`` and never modifies the
stored row — it only adjusts the *in-memory* ``Constraints`` that flow
into the Constructor. The audit trail (multiplier, signal, source
ticker, snapshot id) is returned for the cycle's ``beta_diagnostics``.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from .construction import Constraints
from .models import PortfolioStrategy

log = logging.getLogger(__name__)


@dataclass
class RegimeScalerResult:
    applied: bool
    mode: str  # 'off' | 'scale_net' | 'scale_gross'
    ticker: str
    multiplier: float
    signal: float | None
    pre_target_net_pct: float
    post_target_net_pct: float
    pre_target_gross_pct: float
    post_target_gross_pct: float
    snapshot_id: int | None
    snapshot_as_of: str | None
    reason: str
    allow_negative_net: bool

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "mode": self.mode,
            "ticker": self.ticker,
            "multiplier": round(self.multiplier, 4),
            "signal": (None if self.signal is None else round(self.signal, 4)),
            "pre_target_net_pct": round(self.pre_target_net_pct, 4),
            "post_target_net_pct": round(self.post_target_net_pct, 4),
            "pre_target_gross_pct": round(self.pre_target_gross_pct, 4),
            "post_target_gross_pct": round(self.post_target_gross_pct, 4),
            "snapshot_id": self.snapshot_id,
            "snapshot_as_of": self.snapshot_as_of,
            "reason": self.reason,
            "allow_negative_net": self.allow_negative_net,
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def apply_regime_scaler(
    strategy: PortfolioStrategy,
    constraints: Constraints,
    *,
    as_of: dt.date,
) -> tuple[Constraints, RegimeScalerResult]:
    """Apply the regime exposure scaler if enabled. Returns adjusted
    ``Constraints`` (a new dataclass) and an audit record.

    Default formula (allow_negative_net=False):
        multiplier = clip(0.5 + 0.5 * (bull_prob_1d - bear_prob_1d), floor, ceiling)
    Net-flipping mode (allow_negative_net=True):
        multiplier = clip(signal, -ceiling, ceiling); applied to original net.
    """
    mode = strategy.regime_exposure_scaler
    pre_net = float(constraints.target_net_pct)
    pre_gross = float(constraints.target_gross_pct)
    ticker = (strategy.regime_scaler_ticker or "SPY").upper()
    allow_neg = bool(strategy.regime_scaler_allow_negative_net)
    floor = float(strategy.regime_scaler_floor)
    ceiling = float(strategy.regime_scaler_ceiling)

    if mode == PortfolioStrategy.REGIME_SCALER_OFF:
        return constraints, RegimeScalerResult(
            applied=False, mode="off", ticker=ticker, multiplier=1.0, signal=None,
            pre_target_net_pct=pre_net, post_target_net_pct=pre_net,
            pre_target_gross_pct=pre_gross, post_target_gross_pct=pre_gross,
            snapshot_id=None, snapshot_as_of=None,
            reason="scaler_off", allow_negative_net=allow_neg,
        )

    from hedgefund_agents.macro.regime_persistence import get_latest_snapshot

    snap = get_latest_snapshot(ticker, as_of_date=as_of)
    if snap is None or snap.stale:
        reason = "snapshot_missing" if snap is None else "snapshot_stale"
        return constraints, RegimeScalerResult(
            applied=False, mode=mode, ticker=ticker, multiplier=1.0, signal=None,
            pre_target_net_pct=pre_net, post_target_net_pct=pre_net,
            pre_target_gross_pct=pre_gross, post_target_gross_pct=pre_gross,
            snapshot_id=(snap.pk if snap else None),
            snapshot_as_of=(snap.as_of_date.isoformat() if snap else None),
            reason=reason, allow_negative_net=allow_neg,
        )

    signal = float(snap.bull_minus_bear_1d)
    if allow_neg:
        multiplier = _clamp(signal, -ceiling, ceiling)
    else:
        multiplier = _clamp(0.5 + 0.5 * signal, floor, ceiling)

    new_constraints = Constraints(
        target_gross_pct=pre_gross,
        target_net_pct=pre_net,
        max_position_pct=constraints.max_position_pct,
        max_sector_pct=constraints.max_sector_pct,
        min_position_pct=constraints.min_position_pct,
    )

    post_net = pre_net
    post_gross = pre_gross
    if mode == PortfolioStrategy.REGIME_SCALER_NET:
        post_net = pre_net * multiplier
        new_constraints.target_net_pct = post_net
    elif mode == PortfolioStrategy.REGIME_SCALER_GROSS:
        post_gross = pre_gross * multiplier
        new_constraints.target_gross_pct = post_gross

    return new_constraints, RegimeScalerResult(
        applied=True, mode=mode, ticker=ticker, multiplier=multiplier, signal=signal,
        pre_target_net_pct=pre_net, post_target_net_pct=post_net,
        pre_target_gross_pct=pre_gross, post_target_gross_pct=post_gross,
        snapshot_id=snap.pk, snapshot_as_of=snap.as_of_date.isoformat(),
        reason="ok", allow_negative_net=allow_neg,
    )


def regime_gate_excluded_sleeves(
    strategy: PortfolioStrategy,
    members: list[tuple[str, str]],
    *,
    as_of: dt.date,
) -> dict[str, str]:
    """For P2j: return {ticker: reason} for sleeves to exclude when the gate
    is enabled and a fresh Markov snapshot shows ``bear_prob_5d`` above the
    configured threshold. Empty when the gate is off or no fresh snapshots
    are available.
    """
    if not bool(strategy.enable_markov_regime_gate):
        return {}

    from hedgefund_agents.macro.regime_persistence import get_latest_snapshot

    threshold = float(strategy.markov_bear_prob_5d_threshold)
    excluded: dict[str, str] = {}
    for ticker, _group in members:
        snap = get_latest_snapshot(ticker.upper(), as_of_date=as_of)
        if snap is None or snap.stale:
            continue
        if float(snap.bear_prob_5d) > threshold:
            excluded[ticker] = (
                f"markov_bear_gate(bear_prob_5d={snap.bear_prob_5d:.2f} > "
                f"{threshold:.2f})"
            )
    return excluded


def refresh_regime_snapshots(
    strategy: PortfolioStrategy,
    members: list[tuple[str, str]],
    *,
    as_of: dt.date,
    data_provider,
    include_reference_universe: bool = True,
    config=None,
) -> dict:
    """BYOK-compliant in-cycle Markov refit (2026-07-03 app review §2).

    The 2026-05-26 BYOK-leak fix (`7a93148`) deleted the platform-key prewarm
    assuming cycles refit with the owner's injected provider — but nothing
    did, so RegimeSnapshot rows froze at 2026-05-21: the fund page's Markov
    consensus went permanently "Unavailable" (>10-day staleness) and the P2j
    gate silently no-opped. This closes that loop: at the start of each
    deterministic pod cycle, fit + persist the strategy's sleeve tickers
    (plus the 16-ETF reference universe) using the SAME user-keyed
    ``data_provider`` the cycle already holds.

    Deliberately NOT conditioned on ``enable_markov_regime_gate``: the refit
    is data upkeep for a landing-page surface (the Markov consensus), while
    the gate flag is a *trading* decision (sleeve exclusions) — coupling them
    left the dashboard permanently stale for everyone with the gate off,
    which is the default. ``regime_gate_excluded_sleeves`` still honours the
    flag; refitting with it off changes no trading behavior.

    Policy + cost notes:
    * No platform key is ever touched — the caller injects the owner's
      provider (data-licensing.md). Persisted ``RegimeSnapshot`` rows are
      shared derived market data, same standing as the ``DailyBar`` cache the
      fit reads through (which self-caches, so re-fits are mostly warm).
      Steady-state cost ≈ one bar request per ticker per cycle day.
    * Idempotent per (ticker, as_of, config): a snapshot that already exists
      for this exact date is reused — the first pod cycle of the day fits,
      the other pods (and Run-now / task retries) refetch nothing.
    * Never raises: per-ticker failures (insufficient history, undertrained
      states, provider errors) are logged + reported in the summary — the
      cycle proceeds and the gate stays fail-open, but now *visibly* (see
      ``markov_gate_status``).
    * After any successful fit, the day's stored ``MacroSnapshot.markov_
      consensus`` is recomputed (read-only aggregation, no LLM) so the fund
      page reflects the fresh rows the same evening.

    Returns a summary for the cycle's ``beta_diagnostics``:
    ``{"fitted": [...], "reused": [...], "failed": {ticker: reason}}``.
    """
    from apps.data.models import RegimeSnapshot
    from hedgefund_agents.macro.markov_regime import MarkovConfig
    from hedgefund_agents.macro.regime_persistence import (
        ALWAYS_MODELLED_TICKERS,
        fit_and_persist,
    )

    # Production always uses the DEFAULT MarkovConfig — it must line up with
    # the default-config hash the gate reader queries. ``config`` exists for
    # tests (smaller lookbacks).
    cfg = config or MarkovConfig()
    config_hash = cfg.hash()
    sleeves = [t.upper() for t, _group in members]
    reference = list(ALWAYS_MODELLED_TICKERS) if include_reference_universe else []
    universe = list(dict.fromkeys(sleeves + reference))

    fitted: list[str] = []
    reused: list[str] = []
    failed: dict[str, str] = {}
    for ticker in universe:
        if RegimeSnapshot.objects.filter(
            ticker=ticker,
            as_of_date=as_of,
            model_type=cfg.model_type,
            config_hash=config_hash,
        ).exists():
            reused.append(ticker)
            continue
        try:
            fit_and_persist(
                ticker=ticker, as_of_date=as_of, config=cfg,
                data_provider=data_provider,
            )
            fitted.append(ticker)
        except Exception as exc:
            failed[ticker] = exc.__class__.__name__
            log.warning(
                "regime refit failed for %s @ %s: %s", ticker, as_of, exc,
            )

    if fitted:
        try:
            from hedgefund_agents.macro.regime_persistence import (
                update_stored_consensus,
            )

            update_stored_consensus(as_of)
        except Exception as exc:  # pragma: no cover - consensus refresh is best-effort
            log.warning("markov consensus refresh failed for %s: %s", as_of, exc)

    return {"fitted": fitted, "reused": reused, "failed": failed}


def markov_gate_status(
    strategy: PortfolioStrategy,
    members: list[tuple[str, str]],
    *,
    as_of: dt.date,
) -> str:
    """Honesty flag for cycle diagnostics: is the Markov gate actually live?

    ``regime_gate_excluded_sleeves`` returns an empty map both when every
    sleeve is genuinely below the bear threshold AND when no fresh snapshots
    exist at all — indistinguishable to anyone reading cycle output. This
    disambiguates: 'disabled' | 'active' (≥1 sleeve has a fresh snapshot) |
    'inactive_no_fresh_snapshots' (gate enabled but blind — fail-open).
    """
    if not bool(strategy.enable_markov_regime_gate):
        return "disabled"

    from hedgefund_agents.macro.regime_persistence import get_latest_snapshot

    for ticker, _group in members:
        snap = get_latest_snapshot(ticker.upper(), as_of_date=as_of)
        if snap is not None and not snap.stale:
            return "active"
    return "inactive_no_fresh_snapshots"
