"""In-cycle Markov refit + gate honesty flags (2026-07-03 app review §2).

The 2026-05-26 BYOK fix (`7a93148`) deleted the platform-key prewarm, leaving
``fit_and_persist`` with no production caller: the Markov consensus went
permanently stale (rows frozen at 2026-05-21) and the P2j regime gate
silently failed open. These tests pin the replacement path —
``refresh_regime_snapshots`` refits the sleeve tickers (+ optionally the
16-ETF reference universe) at the start of every deterministic pod cycle
with the owner's injected provider, INDEPENDENT of the gate flag (the refit
is dashboard data upkeep; the flag only controls trading exclusions) — and
``markov_gate_status`` makes the fail-open case visible in diagnostics.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from hedgefund_agents.macro.markov_regime import MarkovConfig
from tests.test_markov_regime import (
    _bear_drift_bars,
    _bull_drift_bars,
    _ephemeral_strategy,
    _ProviderFromBars,
)


class _CountingProvider(_ProviderFromBars):
    """Counts get_daily_bars calls so idempotency tests can assert no refetch."""

    def __init__(self, bars_by_ticker):
        super().__init__(bars_by_ticker)
        self.calls: list[str] = []

    def get_daily_bars(self, ticker, start, end, *, as_of):
        self.calls.append(ticker)
        return super().get_daily_bars(ticker, start, end, as_of=as_of)


def _gate_strategy(**overrides):
    defaults = {
        "enable_markov_regime_gate": True,
        "markov_bear_prob_5d_threshold": Decimal("0.85"),
    }
    defaults.update(overrides)
    return _ephemeral_strategy(**defaults)


AS_OF = dt.date(2025, 1, 2)
END = dt.date(2024, 12, 31)
CFG = MarkovConfig(fit_lookback_observations=500)


# ---------------------------------------------------------------------------
# refresh_regime_snapshots
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_refit_runs_even_when_gate_disabled():
    """The refit is dashboard data upkeep — deliberately NOT conditioned on
    the trading-gate flag (coupling them left the fund page's Markov
    consensus permanently 'Unavailable', since the gate defaults off)."""
    from apps.data.models import RegimeSnapshot
    from apps.portfolios.regime_scaling import refresh_regime_snapshots

    provider = _CountingProvider({"TLT": _bull_drift_bars("TLT", END, 800)})
    strategy = _gate_strategy(enable_markov_regime_gate=False)
    out = refresh_regime_snapshots(
        strategy, [("TLT", "rates")], as_of=AS_OF, data_provider=provider,
        include_reference_universe=False, config=CFG,
    )
    assert out["fitted"] == ["TLT"]
    assert RegimeSnapshot.objects.filter(as_of_date=AS_OF).count() == 1


@pytest.mark.django_db
def test_refit_fits_sleeves_with_injected_provider():
    from apps.data.models import RegimeSnapshot
    from apps.portfolios.regime_scaling import refresh_regime_snapshots

    provider = _CountingProvider({
        "TLT": _bull_drift_bars("TLT", END, 800),
        "GLD": _bear_drift_bars("GLD", END, 800),
    })
    strategy = _gate_strategy()
    out = refresh_regime_snapshots(
        strategy, [("TLT", "rates"), ("GLD", "commodity")],
        as_of=AS_OF, data_provider=provider,
        include_reference_universe=False, config=CFG,
    )
    assert sorted(out["fitted"]) == ["GLD", "TLT"]
    assert out["reused"] == []
    assert out["failed"] == {}
    assert RegimeSnapshot.objects.filter(as_of_date=AS_OF).count() == 2
    assert sorted(provider.calls) == ["GLD", "TLT"]


@pytest.mark.django_db
def test_refit_includes_reference_universe_and_tolerates_failures():
    """Reference-universe tickers without bars fail per-ticker, never raise."""
    from apps.portfolios.regime_scaling import refresh_regime_snapshots
    from hedgefund_agents.macro.regime_persistence import ALWAYS_MODELLED_TICKERS

    # Bars only for the sleeve + SPY; the other 15 reference tickers fail
    # (InsufficientHistoryError inside fit_regime — empty bar lists).
    provider = _CountingProvider({
        "TLT": _bull_drift_bars("TLT", END, 800),
        "SPY": _bull_drift_bars("SPY", END, 800),
    })
    strategy = _gate_strategy()
    out = refresh_regime_snapshots(
        strategy, [("TLT", "rates")], as_of=AS_OF, data_provider=provider,
        config=CFG,
    )
    assert sorted(out["fitted"]) == ["SPY", "TLT"]
    missing = set(ALWAYS_MODELLED_TICKERS) - {"SPY", "TLT"}
    assert set(out["failed"]) == missing
    assert all(reason for reason in out["failed"].values())
    # TLT appears once even though it's both a sleeve and dedup-checked.
    assert provider.calls.count("TLT") == 1


@pytest.mark.django_db
def test_refit_is_idempotent_same_day():
    """A Run-now after the scheduled cycle (same as_of) refetches nothing."""
    from apps.data.models import RegimeSnapshot
    from apps.portfolios.regime_scaling import refresh_regime_snapshots

    provider = _CountingProvider({"TLT": _bull_drift_bars("TLT", END, 800)})
    strategy = _gate_strategy()
    kwargs = dict(
        as_of=AS_OF, data_provider=provider,
        include_reference_universe=False, config=CFG,
    )
    first = refresh_regime_snapshots(strategy, [("TLT", "rates")], **kwargs)
    calls_after_first = len(provider.calls)
    second = refresh_regime_snapshots(strategy, [("TLT", "rates")], **kwargs)

    assert first["fitted"] == ["TLT"]
    assert second == {"fitted": [], "reused": ["TLT"], "failed": {}}
    assert len(provider.calls) == calls_after_first  # no refetch
    assert RegimeSnapshot.objects.count() == 1


# ---------------------------------------------------------------------------
# markov_gate_status
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_gate_status_disabled():
    from apps.portfolios.regime_scaling import markov_gate_status

    strategy = _gate_strategy(enable_markov_regime_gate=False)
    assert markov_gate_status(strategy, [("TLT", "rates")], as_of=AS_OF) == "disabled"


@pytest.mark.django_db
def test_gate_status_inactive_without_fresh_snapshots():
    """Enabled but blind — the fail-open case is now visible, not silent."""
    from apps.portfolios.regime_scaling import markov_gate_status

    strategy = _gate_strategy()
    assert (
        markov_gate_status(strategy, [("TLT", "rates")], as_of=AS_OF)
        == "inactive_no_fresh_snapshots"
    )


@pytest.mark.django_db
def test_gate_status_inactive_when_snapshots_are_stale():
    """Rows older than DEFAULT_STALENESS_DAYS don't make the gate 'active'."""
    from apps.portfolios.regime_scaling import (
        markov_gate_status,
        refresh_regime_snapshots,
    )

    # Fit with the DEFAULT config (what the gate reader queries), dated 30
    # days before the read as_of → stale.
    old_as_of = AS_OF
    read_as_of = AS_OF + dt.timedelta(days=30)
    provider = _ProviderFromBars({"TLT": _bull_drift_bars("TLT", END, 2600)})
    strategy = _gate_strategy()
    out = refresh_regime_snapshots(
        strategy, [("TLT", "rates")], as_of=old_as_of, data_provider=provider,
        include_reference_universe=False,
    )
    assert out["fitted"] == ["TLT"]
    assert (
        markov_gate_status(strategy, [("TLT", "rates")], as_of=read_as_of)
        == "inactive_no_fresh_snapshots"
    )


@pytest.mark.django_db
def test_refit_then_status_active_default_config_end_to_end():
    """Production path: refit with the DEFAULT MarkovConfig writes rows the
    default-config gate reader actually sees (config_hash must line up)."""
    from apps.portfolios.regime_scaling import (
        markov_gate_status,
        refresh_regime_snapshots,
        regime_gate_excluded_sleeves,
    )

    provider = _ProviderFromBars({"TLT": _bull_drift_bars("TLT", END, 2600)})
    strategy = _gate_strategy()
    members = [("TLT", "rates")]
    out = refresh_regime_snapshots(
        strategy, members, as_of=AS_OF, data_provider=provider,
        include_reference_universe=False,
    )
    assert out["fitted"] == ["TLT"]
    assert markov_gate_status(strategy, members, as_of=AS_OF) == "active"
    # The gate evaluates the freshly written row without error. Whether TLT
    # is excluded depends on the fitted state (not pinned here — the point is
    # the config-hash lineup); any exclusion must be well-formed.
    excluded = regime_gate_excluded_sleeves(strategy, members, as_of=AS_OF)
    assert set(excluded) <= {"TLT"}
    assert all("markov_bear_gate" in reason for reason in excluded.values())


@pytest.mark.django_db
def test_refit_updates_stored_macro_consensus():
    """After a fit, the day's MacroSnapshot.markov_consensus is recomputed
    (read-only, no LLM) so the fund page reflects fresh rows the same day."""
    from apps.data.models import MacroSnapshot
    from apps.portfolios.regime_scaling import refresh_regime_snapshots

    macro = MacroSnapshot.objects.create(
        as_of_date=AS_OF, growth_quadrant="recovery", inflation_regime="high",
        yield_curve_state="flat", policy_stance="neutral", narrative="n/a",
        markov_consensus={"consensus_state": "unavailable", "available_count": 0},
    )
    provider = _ProviderFromBars({"TLT": _bull_drift_bars("TLT", END, 800)})
    strategy = _gate_strategy()
    out = refresh_regime_snapshots(
        strategy, [("TLT", "rates")], as_of=AS_OF, data_provider=provider,
        include_reference_universe=False, config=CFG,
    )
    assert out["fitted"] == ["TLT"]
    macro.refresh_from_db()
    # Consensus recomputed over the fresh row — but NOTE: update_stored_
    # consensus aggregates the DEFAULT-config universe/config, while this
    # test fitted with the small CFG. The stored consensus must at least be
    # a recomputed dict (not the seeded placeholder).
    assert macro.markov_consensus is not None
    assert "vote" in macro.markov_consensus
