"""Adversarial review (reviewer: data) — Markov regime classifier design checks.

  * F-CONSENSUS-WEIGHTS: ``compute_markov_consensus`` is an equal-weight head
    count over 16 ETFs (11 of which are S&P sector slices); "sideways 75%"
    literally means 12/16 tickers labelled sideways.
  * F-FIXED-THRESHOLD: the ±5% / 20-day label thresholds are asset-class
    agnostic. For low-vol assets in the reference universe (UUP ~7% vol,
    TLT/GLD/XLP/XLU ~13-16%) the label is structurally "sideways" and the
    diagonal transition probability is ~0.95-0.99 — the consensus is
    dominated by volatility scaling, not by regime.
  * F-MIN-HISTORY: ``min_fit_fraction * fit_lookback_observations`` = 1512
    labelled returns ⇒ ≥ 1532 bars (~6.1 years) or no snapshot at all.
"""
from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

import numpy as np
import pytest

from apps.data.interfaces import Bar
from apps.data.models import RegimeModel, RegimeSnapshot
from hedgefund_agents.macro.markov_regime import (
    InsufficientHistoryError,
    MarkovConfig,
    fit_labelled_markov,
)

pytestmark = pytest.mark.django_db


def _gbm_bars(ticker: str, n: int, annual_vol: float, seed: int, drift: float = 0.06) -> list[Bar]:
    rng = random.Random(seed)
    sigma = annual_vol / (252 ** 0.5)
    mu = drift / 252
    p = 100.0
    out = []
    d = dt.date(2015, 1, 5)
    while len(out) < n:
        if d.weekday() < 5:
            p *= 1.0 + rng.gauss(mu, sigma)
            q = Decimal(str(round(p, 4)))
            out.append(Bar(ticker=ticker, date=d, open=q, high=q, low=q, close=q,
                           adjusted_close=q, volume=1))
        d += dt.timedelta(days=1)
    return out


def _persist_fake_snapshot(ticker: str, state: str, as_of: dt.date) -> None:
    cfg = MarkovConfig()
    rm = RegimeModel.objects.create(
        ticker=ticker, as_of_date=as_of, model_type=cfg.model_type, config_hash=cfg.hash(),
        transition_matrix=[[0.9, 0.05, 0.05], [0.05, 0.9, 0.05], [0.05, 0.05, 0.9]],
        state_labels={}, stationary_distribution={}, fit_observations=1, observations_available=1,
        training_start_date=as_of, training_end_date=as_of,
    )
    RegimeSnapshot.objects.create(
        ticker=ticker, as_of_date=as_of, model_type=cfg.model_type, config_hash=cfg.hash(),
        source_model=rm, last_price_date=as_of, current_state=state,
        current_state_persistence=0.9, bull_persistence=0.9, sideways_persistence=0.9,
        bear_persistence=0.9, bull_prob_1d=0.1, sideways_prob_1d=0.8, bear_prob_1d=0.1,
        bull_prob_5d=0.2, sideways_prob_5d=0.6, bear_prob_5d=0.2, bull_minus_bear_1d=0.0,
    )


def test_consensus_is_an_equal_weight_head_count():
    from hedgefund_agents.macro.regime_persistence import (
        ALWAYS_MODELLED_TICKERS,
        compute_markov_consensus,
    )

    as_of = dt.date(2026, 9, 7)
    states = ["bull", "bull"] + ["sideways"] * 12 + ["bear", "bear"]
    for t, s in zip(ALWAYS_MODELLED_TICKERS, states, strict=True):
        _persist_fake_snapshot(t, s, as_of)
    c = compute_markov_consensus(as_of_date=as_of)
    assert c["vote"] == {"bull": 2, "sideways": 12, "bear": 2}
    assert c["consensus_state"] == "sideways"
    assert c["consensus_strength"] == 0.75
    # SPY + QQQ carry the same weight as XLRE or UUP; the 11 sector slices of
    # the same index are 69% of the vote.
    assert len(ALWAYS_MODELLED_TICKERS) == 16


@pytest.mark.parametrize(
    "label,annual_vol,max_non_sideways_share",
    [
        ("UUP-like", 0.07, 0.05),
        ("TLT/GLD/XLP-like", 0.14, 0.40),
    ],
)
def test_fixed_5pct_threshold_makes_low_vol_assets_structurally_sideways(
    label, annual_vol, max_non_sideways_share
):
    from hedgefund_agents.macro.markov_regime import UndertrainedStateError

    shares = []
    persist = []
    refused = 0
    for seed in range(5):
        bars = _gbm_bars("X", 2600, annual_vol, seed)
        try:
            fit = fit_labelled_markov(ticker="X", as_of_date=dt.date(2026, 9, 7), bars=bars)
        except UndertrainedStateError:
            # <5 bull or bear labels in ~10 years: no snapshot is ever produced
            # for this ticker (it shows up in consensus["missing"]).
            refused += 1
            continue
        counts = fit.state_counts
        non_side = (counts["bull"] + counts["bear"]) / sum(counts.values())
        shares.append(non_side)
        persist.append(fit.sideways_persistence)
    assert (not shares) or max(shares) <= max_non_sideways_share, (label, shares, refused)
    assert (not persist) or min(persist) >= 0.9, (label, persist)
    if annual_vol < 0.1:
        assert refused >= 1, "a 7%-vol asset should be refused/undertrained at least sometimes"


def test_spy_like_vol_spends_a_third_of_the_time_outside_sideways():
    shares = []
    for seed in range(5):
        bars = _gbm_bars("SPY", 2600, 0.18, seed)
        fit = fit_labelled_markov(ticker="SPY", as_of_date=dt.date(2026, 9, 7), bars=bars)
        c = fit.state_counts
        shares.append((c["bull"] + c["bear"]) / sum(c.values()))
    # Same model, same thresholds: an 18%-vol asset is non-sideways ~30-45% of
    # days while a 7%-vol asset is <5%. The "vote" therefore measures vol.
    assert np.mean(shares) > 0.25


def test_six_years_of_history_required_for_any_snapshot():
    bars = _gbm_bars("IPO", 1500, 0.3, 1)  # ~6 years of sessions
    with pytest.raises(InsufficientHistoryError):
        fit_labelled_markov(ticker="IPO", as_of_date=dt.date(2026, 9, 7), bars=bars)
    bars = _gbm_bars("OLD", 1540, 0.3, 1)
    fit_labelled_markov(ticker="OLD", as_of_date=dt.date(2026, 9, 7), bars=bars)
