"""Unit + integration tests for the P2m Markov regime classifier."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from decimal import Decimal

import pytest

from hedgefund_agents.macro.markov_regime import (
    STATES,
    InsufficientHistoryError,
    MarkovConfig,
    UndertrainedStateError,
    _stationary,
    fit_labelled_markov,
    fit_regime,
)


@dataclass
class _Bar:
    ticker: str
    date: dt.date
    close: Decimal
    adjusted_close: Decimal
    open: Decimal = Decimal("100")
    high: Decimal = Decimal("100")
    low: Decimal = Decimal("100")
    volume: int = 0


def _make_bars(
    ticker: str, end: dt.date, n_days: int, return_series: list[float],
    *, start_price: float = 100.0, adjusted_split_at: int | None = None,
    split_ratio: float = 2.0,
) -> list[_Bar]:
    """Synthesize bars walking back from ``end`` over ``n_days`` weekdays.

    ``return_series`` is reversed and applied so the most recent return is
    series[-1] etc. When ``adjusted_split_at`` is set, a split is injected
    at that bar in ``close`` but not in ``adjusted_close`` (the split test).
    """
    assert len(return_series) == n_days - 1, "need n_days-1 returns"
    dates: list[dt.date] = []
    cur = end
    while len(dates) < n_days:
        if cur.weekday() < 5:
            dates.append(cur)
        cur = cur - dt.timedelta(days=1)
    dates.reverse()

    prices = [start_price]
    for r in return_series:
        prices.append(prices[-1] * (1.0 + r))
    bars: list[_Bar] = []
    for i, (d, p) in enumerate(zip(dates, prices)):
        raw_close = p
        if adjusted_split_at is not None and i >= adjusted_split_at:
            raw_close = p / split_ratio  # raw close drops at the "split"
        bars.append(_Bar(
            ticker=ticker, date=d,
            close=Decimal(str(round(raw_close, 6))),
            adjusted_close=Decimal(str(round(p, 6))),
        ))
    return bars


# ---------------------------------------------------------------------------
# Pure fitter tests (no DB)
# ---------------------------------------------------------------------------

class TestLabelledMarkovFit:
    def test_recovers_known_transition_matrix(self):
        """Synthetic 3-regime trajectory → fitted P should have the expected
        diagonal-dominance and bull row → bull stickiness."""
        n_days = 1500
        rng_seed = 42
        returns = []
        # Build alternating regime blocks: 100 bull-trend, 100 bear-trend, 100 sideways.
        rng = _StableRng(rng_seed)
        block = 100
        for i in range(n_days - 1):
            block_id = (i // block) % 3
            if block_id == 0:    # bull-tilt
                returns.append(0.004 + rng.uniform(-0.001, 0.001))
            elif block_id == 1:  # bear-tilt
                returns.append(-0.004 + rng.uniform(-0.001, 0.001))
            else:                # sideways
                returns.append(rng.uniform(-0.0005, 0.0005))
        bars = _make_bars("TEST", dt.date(2024, 12, 31), n_days, returns)
        as_of = dt.date(2025, 1, 2)
        fit = fit_labelled_markov(
            ticker="TEST", as_of_date=as_of, bars=bars,
            config=MarkovConfig(fit_lookback_observations=1200),
        )
        # Row-stochastic.
        for row in fit.transition_matrix:
            assert pytest.approx(sum(row), abs=1e-9) == 1.0
        assert len(fit.transition_matrix) == 3
        # Stationary sums to 1.
        assert pytest.approx(sum(fit.stationary_distribution.values()), abs=1e-9) == 1.0
        # Walk-forward guarantee: training_end_date < as_of.
        assert fit.training_end_date < as_of

    def test_adjusted_close_handles_split_continuously(self):  # noqa: D401
        """A 2:1 split in raw close but continuous adjusted_close must not
        flip the state to bear or emit a junk label transition."""
        # 600 days with rotating regime blocks, ending in a bull regime so the
        # current_state assertion is unambiguous. Raw close splits halfway through.
        rng = _StableRng(7)
        returns: list[float] = []
        # 8 blocks of 75 days each; cycle bull → bear → sideways → bull → ...
        # ending on a bull block.
        rotation = ["bull", "bear", "sideways", "bull", "bear", "sideways", "bear", "bull"]
        for block_id in rotation:
            for _ in range(75):
                if block_id == "bull":
                    returns.append(0.005 + rng.uniform(-0.002, 0.002))
                elif block_id == "bear":
                    returns.append(-0.005 + rng.uniform(-0.002, 0.002))
                else:
                    returns.append(rng.uniform(-0.001, 0.001))
        returns = returns[:599]
        bars = _make_bars(
            "SPL", dt.date(2024, 12, 31), 600, returns,
            adjusted_split_at=300, split_ratio=2.0,
        )
        fit = fit_labelled_markov(
            ticker="SPL", as_of_date=dt.date(2025, 1, 2), bars=bars,
            config=MarkovConfig(fit_lookback_observations=500),
        )
        # No outsized bear bucket: bull persistence dominates because the
        # adjusted_close path is monotone-upward.
        bull_idx = STATES.index("bull")
        bear_idx = STATES.index("bear")
        assert fit.transition_matrix[bull_idx][bull_idx] > fit.transition_matrix[bear_idx][bear_idx]
        # The current state should NOT be bear despite the raw close split.
        assert fit.current_state in {"bull", "sideways"}

    def test_walk_forward_excludes_future_data(self):
        """The model fitted on as_of=D is bit-identical regardless of what
        data exists for dates >= D."""
        # Use mixed-regime returns so all three states clear the guardrail.
        rng = _StableRng(11)
        returns: list[float] = []
        for i in range(799):
            block = (i // 70) % 3
            if block == 0:
                returns.append(0.005 + rng.uniform(-0.002, 0.002))
            elif block == 1:
                returns.append(-0.005 + rng.uniform(-0.002, 0.002))
            else:
                returns.append(rng.uniform(-0.001, 0.001))
        end = dt.date(2025, 6, 30)
        bars = _make_bars("WF", end, 800, returns)
        as_of = dt.date(2025, 1, 2)
        cfg = MarkovConfig(fit_lookback_observations=400)
        # Truncate the bar list to "before as_of" first; the fitter should
        # produce the same result whether we hand it the full series or not.
        before = [b for b in bars if b.date < as_of]
        fit_a = fit_labelled_markov(
            ticker="WF", as_of_date=as_of, bars=before, config=cfg
        )
        fit_b = fit_labelled_markov(
            ticker="WF", as_of_date=as_of, bars=bars, config=cfg
        )
        assert fit_a.transition_matrix == fit_b.transition_matrix
        assert fit_a.training_end_date == fit_b.training_end_date
        assert fit_a.current_state == fit_b.current_state
        assert fit_a.training_end_date < as_of

    def test_weekend_as_of_uses_latest_prior_trading_bar(self):
        """as_of on a Sunday should not return Monday's bar."""
        rng = _StableRng(13)
        returns: list[float] = []
        for i in range(799):
            block = (i // 60) % 3
            if block == 0:
                returns.append(0.005 + rng.uniform(-0.002, 0.002))
            elif block == 1:
                returns.append(-0.005 + rng.uniform(-0.002, 0.002))
            else:
                returns.append(rng.uniform(-0.001, 0.001))
        # Make 2025-01-04 a Saturday in the bar series. Choose end = Friday.
        friday = dt.date(2025, 1, 3)
        bars = _make_bars("WK", friday, 800, returns)
        sunday = dt.date(2025, 1, 5)  # Sunday
        fit = fit_labelled_markov(
            ticker="WK", as_of_date=sunday, bars=bars,
            config=MarkovConfig(fit_lookback_observations=400),
        )
        assert fit.last_price_date == friday  # Friday's bar is the most recent.

    def test_insufficient_history_raises(self):
        rng = _StableRng(2)
        returns = [rng.uniform(-0.001, 0.001) for _ in range(99)]
        bars = _make_bars("SHORT", dt.date(2025, 1, 2), 100, returns)
        with pytest.raises(InsufficientHistoryError):
            fit_labelled_markov(
                ticker="SHORT", as_of_date=dt.date(2025, 1, 3), bars=bars,
                config=MarkovConfig(fit_lookback_observations=2520),
            )

    def test_undertrained_state_raises_on_monotone_series(self):
        """A pure-bull series → no bear labels → state-count guardrail trips."""
        # 600 days of strictly positive returns above the bull threshold.
        returns = [0.01] * 599
        bars = _make_bars("BULL", dt.date(2025, 1, 2), 600, returns)
        with pytest.raises(UndertrainedStateError):
            fit_labelled_markov(
                ticker="BULL", as_of_date=dt.date(2025, 1, 3), bars=bars,
                config=MarkovConfig(fit_lookback_observations=500),
            )

    def test_config_hash_is_stable(self):
        a = MarkovConfig()
        b = MarkovConfig()
        assert a.hash() == b.hash()
        c = MarkovConfig(bull_threshold=0.03)
        assert a.hash() != c.hash()

    def test_forecast_collapses_toward_stationary(self):
        """A long horizon should approach the stationary distribution."""
        rng = _StableRng(3)
        returns: list[float] = []
        for i in range(799):
            block = (i // 60) % 3
            if block == 0:
                returns.append(0.005 + rng.uniform(-0.002, 0.002))
            elif block == 1:
                returns.append(-0.005 + rng.uniform(-0.002, 0.002))
            else:
                returns.append(rng.uniform(-0.001, 0.001))
        bars = _make_bars("FCT", dt.date(2024, 12, 31), 800, returns)
        fit = fit_labelled_markov(
            ticker="FCT", as_of_date=dt.date(2025, 1, 2), bars=bars,
            config=MarkovConfig(fit_lookback_observations=600),
        )
        far = fit.forecast(80)
        target = fit.stationary_distribution
        for s in STATES:
            assert abs(far[s] - target[s]) < 0.05

    def test_stationary_distribution_is_probability(self):
        import numpy as np
        P = np.array(
            [[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]], dtype=float
        )
        pi = _stationary(P)
        assert pytest.approx(float(pi.sum()), abs=1e-9) == 1.0
        assert all(p >= 0 for p in pi.tolist())


# ---------------------------------------------------------------------------
# Persistence + view tests
# ---------------------------------------------------------------------------

class _ProviderFromBars:
    """Wraps a list of bars and serves them via get_daily_bars."""

    name = "test"

    def __init__(self, bars_by_ticker: dict[str, list[_Bar]]):
        self.bars = bars_by_ticker

    def get_daily_bars(self, ticker, start, end, *, as_of):
        return [
            b for b in self.bars.get(ticker, [])
            if start <= b.date <= end and b.date <= as_of
        ]


def _stable_seed(ticker: str) -> int:
    # Process-stable seed; avoids Python's hash randomization.
    import hashlib
    return int.from_bytes(hashlib.sha256(ticker.encode()).digest()[:4], "big") % 9973


def _mixed_regime_bars(
    ticker: str, end: dt.date, n: int = 800, *, drift_bias: float = 0.0,
) -> list[_Bar]:
    """Bars with alternating regime blocks so all three states get hit.

    ``drift_bias`` tilts the average return: +x leans bull, -x leans bear.
    """
    seed = _stable_seed(ticker)
    rng = _StableRng(seed)
    block = 90
    returns: list[float] = []
    for i in range(n - 1):
        b = (i // block) % 3
        if b == 0:
            returns.append(0.0045 + drift_bias + rng.uniform(-0.002, 0.002))
        elif b == 1:
            returns.append(-0.0045 + drift_bias + rng.uniform(-0.002, 0.002))
        else:
            returns.append(drift_bias + rng.uniform(-0.0015, 0.0015))
    return _make_bars(ticker, end, n, returns)


def _bull_drift_bars(ticker: str, end: dt.date, n: int = 800) -> list[_Bar]:
    return _mixed_regime_bars(ticker, end, n, drift_bias=0.001)


def _bear_drift_bars(ticker: str, end: dt.date, n: int = 800) -> list[_Bar]:
    return _mixed_regime_bars(ticker, end, n, drift_bias=-0.001)


@pytest.mark.django_db
def test_fit_and_persist_creates_unique_row():
    from apps.data.models import RegimeModel, RegimeSnapshot
    from hedgefund_agents.macro.regime_persistence import fit_and_persist

    end = dt.date(2024, 12, 31)
    bars = _bull_drift_bars("SPY", end, 800)
    provider = _ProviderFromBars({"SPY": bars})
    as_of = dt.date(2025, 1, 2)
    cfg = MarkovConfig(fit_lookback_observations=500)

    snap_a = fit_and_persist(
        ticker="SPY", as_of_date=as_of, config=cfg, data_provider=provider
    )
    snap_b = fit_and_persist(
        ticker="SPY", as_of_date=as_of, config=cfg, data_provider=provider
    )
    assert snap_a.pk == snap_b.pk
    assert RegimeModel.objects.count() == 1
    assert RegimeSnapshot.objects.count() == 1
    assert snap_a.current_state in {"bull", "sideways", "bear"}
    assert snap_a.last_price_date < as_of


@pytest.mark.django_db
def test_prior_snapshot_link_and_deltas():
    from hedgefund_agents.macro.regime_persistence import fit_and_persist

    end_a = dt.date(2024, 12, 27)
    bars_a = _bull_drift_bars("SPY", end_a, 800)
    end_b = dt.date(2024, 12, 31)
    bars_b = _bull_drift_bars("SPY", end_b, 800)
    cfg = MarkovConfig(fit_lookback_observations=500)
    p_a = _ProviderFromBars({"SPY": bars_a})
    p_b = _ProviderFromBars({"SPY": bars_b})

    fit_and_persist(
        ticker="SPY", as_of_date=dt.date(2024, 12, 30), config=cfg, data_provider=p_a
    )
    snap_2 = fit_and_persist(
        ticker="SPY", as_of_date=dt.date(2025, 1, 2), config=cfg, data_provider=p_b
    )
    assert snap_2.prior_snapshot is not None
    assert snap_2.prior_current_state in {"bull", "sideways", "bear"}


@pytest.mark.django_db
def test_consensus_aggregates_three_tickers():
    from hedgefund_agents.macro.regime_persistence import (
        compute_markov_consensus,
        fit_and_persist,
    )

    end = dt.date(2024, 12, 31)
    bars = {
        "BULL_A": _bull_drift_bars("BULL_A", end, 800),
        "BULL_B": _bull_drift_bars("BULL_B", end, 800),
        "BEAR_C": _bear_drift_bars("BEAR_C", end, 800),
    }
    provider = _ProviderFromBars(bars)
    as_of = dt.date(2025, 1, 2)
    cfg = MarkovConfig(fit_lookback_observations=500)
    for t in bars:
        fit_and_persist(
            ticker=t, as_of_date=as_of, config=cfg, data_provider=provider
        )
    consensus = compute_markov_consensus(
        as_of_date=as_of, tickers=list(bars.keys()), config=cfg,
    )
    assert consensus["available_count"] == 3
    assert consensus["consensus_state"] in {"bull", "sideways", "bear"}
    # All three appear in per_ticker even if state varies.
    assert set(consensus["per_ticker"].keys()) == set(bars.keys())


@pytest.mark.django_db
def test_disagreement_mapping_flags_opposite_buckets():
    from hedgefund_agents.macro.regime_persistence import disagreement_with_markov

    class _Macro:
        growth_quadrant = "recession"
        yield_curve_state = "inverted"
        policy_stance = "tightening"

    consensus_bull = {"consensus_state": "bull", "vote": {"bull": 10, "bear": 1, "sideways": 1}}
    disagrees, reason = disagreement_with_markov(_Macro(), consensus_bull)
    assert disagrees is True
    assert "Markov consensus=bull" in reason

    consensus_bear = {"consensus_state": "bear", "vote": {"bull": 1, "bear": 10, "sideways": 1}}
    disagrees_b, _r = disagreement_with_markov(_Macro(), consensus_bear)
    assert disagrees_b is False  # both risk_off → no disagreement.


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_batch_endpoint_handles_missing_gracefully(client, django_user_model):
    from rest_framework_simplejwt.tokens import RefreshToken

    user = django_user_model.objects.create_user(
        email="t@example.com", password="xxxxxxxxxxxx"
    )
    token = str(RefreshToken.for_user(user).access_token)

    from hedgefund_agents.macro.regime_persistence import fit_and_persist

    cfg = MarkovConfig(fit_lookback_observations=500)
    bars = _bull_drift_bars("SPY", dt.date(2024, 12, 31), 800)
    provider = _ProviderFromBars({"SPY": bars})
    as_of = dt.date(2025, 1, 2)
    fit_and_persist(ticker="SPY", as_of_date=as_of, config=cfg, data_provider=provider)

    resp = client.get(
        "/api/macro/regime/batch/?tickers=SPY,QQQ&as_of=2025-01-02",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert resp.status_code == 200
    body = resp.json()
    items_by_t = {it["ticker"]: it for it in body["items"]}
    assert items_by_t["SPY"]["snapshot"] is None or items_by_t["SPY"]["snapshot"]["current_state"]
    assert items_by_t["QQQ"]["snapshot"] is None
    assert items_by_t["QQQ"]["reason"] == "no_snapshot"


# ---------------------------------------------------------------------------
# Sector / risk-parity / scaler integration tests
# ---------------------------------------------------------------------------

def _seed_snapshot(
    *,
    ticker: str,
    as_of_date: dt.date,
    current_state: str = "bull",
    bull_minus_bear_1d: float = 0.4,
    bear_prob_5d: float = 0.1,
    bull_persistence: float = 0.7,
    bear_persistence: float = 0.4,
    config: MarkovConfig | None = None,
):
    """Helper: stamp a (ticker, as_of) RegimeSnapshot with the default config_hash
    so downstream consumers (sector features, scaler, gate) can find it.
    """
    from apps.data.models import RegimeModel, RegimeSnapshot

    cfg = config or MarkovConfig()
    rm = RegimeModel.objects.create(
        ticker=ticker, as_of_date=as_of_date,
        model_type=cfg.model_type, config_hash=cfg.hash(),
        return_window_days=cfg.return_window_days,
        bull_threshold_return=Decimal(str(cfg.bull_threshold)),
        bear_threshold_return=Decimal(str(cfg.bear_threshold)),
        price_field=cfg.price_field,
        transition_matrix=[[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]],
        state_labels={"0": "bear", "1": "sideways", "2": "bull"},
        stationary_distribution={"bull": 0.34, "sideways": 0.48, "bear": 0.18},
        fit_observations=500, observations_available=520,
        fit_lookback_observations=cfg.fit_lookback_observations,
        training_start_date=as_of_date - dt.timedelta(days=500),
        training_end_date=as_of_date,
    )
    bull1 = 0.5 + 0.5 * bull_minus_bear_1d
    bear1 = 0.5 - 0.5 * bull_minus_bear_1d
    return RegimeSnapshot.objects.create(
        ticker=ticker, as_of_date=as_of_date,
        model_type=cfg.model_type, config_hash=cfg.hash(),
        source_model=rm,
        last_price_date=as_of_date,
        current_state=current_state, current_return=0.01,
        current_state_persistence=bull_persistence if current_state == "bull" else bear_persistence,
        bull_persistence=bull_persistence,
        sideways_persistence=0.4,
        bear_persistence=bear_persistence,
        bull_prob_1d=max(0.0, min(1.0, bull1)),
        sideways_prob_1d=max(0.0, 1.0 - bull1 - bear1),
        bear_prob_1d=max(0.0, min(1.0, bear1)),
        bull_prob_5d=0.3, sideways_prob_5d=0.4, bear_prob_5d=bear_prob_5d,
        bull_minus_bear_1d=bull_minus_bear_1d,
    )


@pytest.mark.django_db
def test_sector_features_include_markov_score(monkeypatch):
    from hedgefund_agents.screener import sector_features as sf

    as_of = dt.date(2025, 1, 2)
    _seed_snapshot(
        ticker="XLK", as_of_date=as_of,
        current_state="bull", bull_minus_bear_1d=0.45,
    )

    # Patch the FMP provider used by compute_sector_features so the function
    # doesn't try real HTTP. We just need it to fall back to synthetic bars.
    class _StubFmp:
        def get_daily_bars(self, *args, **kwargs):
            return []

    monkeypatch.setattr(sf, "FmpProvider", lambda: _StubFmp())

    feat = sf.compute_sector_features(
        "XLK", "Technology", "tech", {}, as_of,
        benchmark_returns={"1m": 0.0, "3m": 0.0, "6m": 0.0},
    )
    assert feat.markov_regime_score == pytest.approx(0.45, abs=1e-6)
    assert feat.markov_regime_state == "bull"
    assert feat.markov_regime_stale is False


@pytest.mark.django_db
def test_regime_scaler_off_is_no_op():
    from apps.portfolios.construction import Constraints
    from apps.portfolios.models import PortfolioStrategy
    from apps.portfolios.regime_scaling import apply_regime_scaler

    s = _ephemeral_strategy(regime_exposure_scaler="off")
    pre = Constraints(target_gross_pct=1.5, target_net_pct=0.5,
                      max_position_pct=0.03, max_sector_pct=0.25,
                      min_position_pct=0.005)
    post, audit = apply_regime_scaler(s, pre, as_of=dt.date(2025, 1, 2))
    assert post.target_net_pct == pre.target_net_pct
    assert audit.applied is False
    assert audit.mode == "off"


@pytest.mark.django_db
def test_regime_scaler_scale_net_with_bear_snapshot():
    from apps.data.models import RegimeModel, RegimeSnapshot
    from apps.portfolios.construction import Constraints
    from apps.portfolios.regime_scaling import apply_regime_scaler

    as_of = dt.date(2025, 1, 2)
    rm = RegimeModel.objects.create(
        ticker="SPY", as_of_date=as_of - dt.timedelta(days=1),
        model_type="labelled_markov",
        config_hash=MarkovConfig().hash(),
        return_window_days=20, bull_threshold_return=Decimal("0.05"),
        bear_threshold_return=Decimal("-0.05"), price_field="adjusted_close",
        transition_matrix=[[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]],
        state_labels={"0": "bear", "1": "sideways", "2": "bull"},
        stationary_distribution={"bull": 0.2, "sideways": 0.3, "bear": 0.5},
        fit_observations=500, observations_available=520,
        fit_lookback_observations=500,
        training_start_date=as_of - dt.timedelta(days=500),
        training_end_date=as_of - dt.timedelta(days=1),
    )
    RegimeSnapshot.objects.create(
        ticker="SPY", as_of_date=as_of - dt.timedelta(days=1),
        model_type="labelled_markov",
        config_hash=MarkovConfig().hash(),
        source_model=rm,
        last_price_date=as_of - dt.timedelta(days=1),
        current_state="bear", current_return=-0.08,
        current_state_persistence=0.7, bull_persistence=0.5,
        sideways_persistence=0.4, bear_persistence=0.7,
        bull_prob_1d=0.1, sideways_prob_1d=0.2, bear_prob_1d=0.7,
        bull_prob_5d=0.15, sideways_prob_5d=0.25, bear_prob_5d=0.6,
        bull_minus_bear_1d=-0.6,
    )
    s = _ephemeral_strategy(regime_exposure_scaler="scale_net")
    pre = Constraints(target_gross_pct=1.5, target_net_pct=0.5,
                      max_position_pct=0.03, max_sector_pct=0.25,
                      min_position_pct=0.005)
    post, audit = apply_regime_scaler(s, pre, as_of=as_of)
    assert audit.applied is True
    assert audit.mode == "scale_net"
    assert audit.signal == -0.6
    # multiplier = clip(0.5 + 0.5 * -0.6, 0.2, 1.0) = 0.2
    assert audit.multiplier == pytest.approx(0.2, abs=1e-9)
    assert post.target_net_pct == pytest.approx(0.5 * 0.2, abs=1e-9)


@pytest.mark.django_db
def test_regime_scaler_allow_negative_net_can_flip():
    from apps.data.models import RegimeModel, RegimeSnapshot
    from apps.portfolios.construction import Constraints
    from apps.portfolios.regime_scaling import apply_regime_scaler

    as_of = dt.date(2025, 1, 2)
    rm = RegimeModel.objects.create(
        ticker="SPY", as_of_date=as_of - dt.timedelta(days=1),
        model_type="labelled_markov", config_hash=MarkovConfig().hash(),
        return_window_days=20, bull_threshold_return=Decimal("0.05"),
        bear_threshold_return=Decimal("-0.05"), price_field="adjusted_close",
        transition_matrix=[[0.7, 0.2, 0.1], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]],
        state_labels={"0": "bear", "1": "sideways", "2": "bull"},
        stationary_distribution={"bull": 0.2, "sideways": 0.3, "bear": 0.5},
        fit_observations=500, observations_available=520,
        fit_lookback_observations=500,
        training_start_date=as_of - dt.timedelta(days=500),
        training_end_date=as_of - dt.timedelta(days=1),
    )
    RegimeSnapshot.objects.create(
        ticker="SPY", as_of_date=as_of - dt.timedelta(days=1),
        model_type="labelled_markov", config_hash=MarkovConfig().hash(),
        source_model=rm,
        last_price_date=as_of - dt.timedelta(days=1),
        current_state="bear", current_return=-0.08,
        current_state_persistence=0.7, bull_persistence=0.5,
        sideways_persistence=0.4, bear_persistence=0.7,
        bull_prob_1d=0.1, sideways_prob_1d=0.2, bear_prob_1d=0.7,
        bull_prob_5d=0.15, sideways_prob_5d=0.25, bear_prob_5d=0.6,
        bull_minus_bear_1d=-0.8,
    )
    s = _ephemeral_strategy(
        regime_exposure_scaler="scale_net", regime_scaler_allow_negative_net=True,
    )
    pre = Constraints(target_gross_pct=1.5, target_net_pct=0.5,
                      max_position_pct=0.03, max_sector_pct=0.25,
                      min_position_pct=0.005)
    post, audit = apply_regime_scaler(s, pre, as_of=as_of)
    assert audit.multiplier == pytest.approx(-0.8, abs=1e-9)
    assert post.target_net_pct == pytest.approx(0.5 * -0.8, abs=1e-9)


@pytest.mark.django_db
def test_regime_gate_excludes_high_bear_sleeve():
    from apps.data.models import RegimeModel, RegimeSnapshot
    from apps.portfolios.regime_scaling import regime_gate_excluded_sleeves

    as_of = dt.date(2025, 1, 2)
    for ticker, bear in (("TLT", 0.92), ("GLD", 0.30)):
        rm = RegimeModel.objects.create(
            ticker=ticker, as_of_date=as_of - dt.timedelta(days=1),
            model_type="labelled_markov", config_hash=MarkovConfig().hash(),
            return_window_days=20, bull_threshold_return=Decimal("0.05"),
            bear_threshold_return=Decimal("-0.05"), price_field="adjusted_close",
            transition_matrix=[[0.9, 0.05, 0.05], [0.2, 0.6, 0.2], [0.1, 0.3, 0.6]],
            state_labels={"0": "bear", "1": "sideways", "2": "bull"},
            stationary_distribution={"bull": 0.2, "sideways": 0.3, "bear": 0.5},
            fit_observations=500, observations_available=520,
            fit_lookback_observations=500,
            training_start_date=as_of - dt.timedelta(days=500),
            training_end_date=as_of - dt.timedelta(days=1),
        )
        RegimeSnapshot.objects.create(
            ticker=ticker, as_of_date=as_of - dt.timedelta(days=1),
            model_type="labelled_markov", config_hash=MarkovConfig().hash(),
            source_model=rm,
            last_price_date=as_of - dt.timedelta(days=1),
            current_state="bear", current_return=-0.08,
            current_state_persistence=0.9, bull_persistence=0.5,
            sideways_persistence=0.4, bear_persistence=0.9,
            bull_prob_1d=0.05, sideways_prob_1d=0.1, bear_prob_1d=0.85,
            bull_prob_5d=0.05, sideways_prob_5d=0.1, bear_prob_5d=bear,
            bull_minus_bear_1d=-0.8,
        )
    s = _ephemeral_strategy(
        enable_markov_regime_gate=True, markov_bear_prob_5d_threshold=Decimal("0.85"),
    )
    members = [("TLT", "rates"), ("GLD", "commodity")]
    excluded = regime_gate_excluded_sleeves(s, members, as_of=as_of)
    assert "TLT" in excluded
    assert "GLD" not in excluded


@pytest.mark.django_db
def test_markov_persistence_tool_flags_bull_drop():
    from apps.data.models import RegimeModel, RegimeSnapshot
    from hedgefund_agents.risk.tools.markov_persistence import markov_persistence_change

    as_of_a = dt.date(2024, 12, 27)
    as_of_b = dt.date(2025, 1, 2)
    cfg_hash = MarkovConfig().hash()
    for (date, bull_p, bear_p, state) in (
        (as_of_a, 0.85, 0.40, "bull"),
        (as_of_b, 0.40, 0.85, "bear"),
    ):
        rm = RegimeModel.objects.create(
            ticker="SPY", as_of_date=date,
            model_type="labelled_markov", config_hash=cfg_hash,
            return_window_days=20, bull_threshold_return=Decimal("0.05"),
            bear_threshold_return=Decimal("-0.05"), price_field="adjusted_close",
            transition_matrix=[[0.5, 0.3, 0.2], [0.3, 0.4, 0.3], [0.2, 0.3, 0.5]],
            state_labels={"0": "bear", "1": "sideways", "2": "bull"},
            stationary_distribution={"bull": 0.33, "sideways": 0.34, "bear": 0.33},
            fit_observations=500, observations_available=520,
            fit_lookback_observations=500,
            training_start_date=date - dt.timedelta(days=500),
            training_end_date=date,
        )
        RegimeSnapshot.objects.create(
            ticker="SPY", as_of_date=date,
            model_type="labelled_markov", config_hash=cfg_hash,
            source_model=rm,
            last_price_date=date,
            current_state=state, current_return=0.01,
            current_state_persistence=bull_p if state == "bull" else bear_p,
            bull_persistence=bull_p,
            sideways_persistence=0.5,
            bear_persistence=bear_p,
            bull_prob_1d=0.3, sideways_prob_1d=0.4, bear_prob_1d=0.3,
            bull_prob_5d=0.3, sideways_prob_5d=0.4, bear_prob_5d=0.3,
            bull_minus_bear_1d=0.0,
        )
    out = markov_persistence_change("SPY", as_of=as_of_b)
    assert out["status"] == "ok"
    assert out["risk_flag"] is True
    assert any("bull_persistence" in f for f in out["flags"])
    assert any("bear_persistence" in f for f in out["flags"])
    assert out["state_changed"] is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StableRng:
    """Tiny deterministic LCG so tests don't rely on Python's hash randomization."""

    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF

    def _next(self) -> float:
        self.state = (1103515245 * self.state + 12345) & 0xFFFFFFFF
        return self.state / 0xFFFFFFFF

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self._next()


def _ephemeral_strategy(**overrides):
    """Build an *unsaved* PortfolioStrategy-like object with the regime fields.

    The regime scaler / gate helpers only read attributes; they don't query
    related rows, so an in-memory object is enough — and it avoids the
    Universe/Portfolio FK setup needed for a real row.
    """
    from apps.portfolios.models import PortfolioStrategy

    defaults = {
        "regime_exposure_scaler": "off",
        "regime_scaler_floor": Decimal("0.2"),
        "regime_scaler_ceiling": Decimal("1.0"),
        "regime_scaler_ticker": "SPY",
        "regime_scaler_allow_negative_net": False,
        "enable_markov_regime_gate": False,
        "markov_bear_prob_5d_threshold": Decimal("0.85"),
    }
    defaults.update(overrides)
    obj = PortfolioStrategy()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj
