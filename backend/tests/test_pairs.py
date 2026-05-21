"""P2k — pairs trading screener tests."""
from __future__ import annotations

import math
from datetime import date

import pytest

from apps.portfolios.pairs import (
    _adf_like_p,
    _ols_alpha_beta,
    evaluate_pair,
)


def _ou_pair(n: int = 300, beta: float = 1.0, sigma_noise: float = 0.02, seed: int = 7):
    """Generate a synthetic cointegrated pair.

    P_b is a random walk; P_a = exp(α + β·ln(P_b) + ε_t) with ε mean-reverting.
    """
    import random
    rng = random.Random(seed)
    lb = [math.log(50.0)]
    for _ in range(n - 1):
        lb.append(lb[-1] + rng.gauss(0, 0.01))
    # Mean-reverting ε via AR(1) with ρ=0.7 → strongly stationary.
    eps = [0.0]
    for _ in range(n - 1):
        eps.append(0.7 * eps[-1] + rng.gauss(0, sigma_noise))
    alpha = math.log(2.0)
    la = [alpha + beta * lb[i] + eps[i] for i in range(n)]
    return la, lb


def test_ols_recovers_known_hedge_ratio():
    """Plan bar: OLS β within ±10% of the known synthetic β."""
    la, lb = _ou_pair(n=400, beta=1.0, sigma_noise=0.015)
    _alpha, beta_hat = _ols_alpha_beta(la, lb)
    assert abs(beta_hat - 1.0) < 0.10, f"recovered β={beta_hat}"


def test_cointegrated_pair_passes_screen():
    """Plan bar: an OU-driven cointegrated pair must score ADF p<0.05 and
    recover a hedge ratio in [0.90, 1.10] for known β=1.0."""
    la, lb = _ou_pair(n=400, beta=1.0, sigma_noise=0.015)
    cand = evaluate_pair("A", "B", "Tech", la, lb, synthetic_pair=False)
    assert cand is not None
    assert cand.p_value < 0.05, f"p_value={cand.p_value} too high for cointegrated pair"
    assert cand.correlation > 0.80
    assert 0.90 <= cand.hedge_ratio <= 1.10, f"β={cand.hedge_ratio}"


def test_independent_random_walks_fail_to_cointegrate():
    import random
    rng = random.Random(11)
    la = [math.log(50.0)]
    lb = [math.log(40.0)]
    for _ in range(399):
        la.append(la[-1] + rng.gauss(0, 0.012))
        lb.append(lb[-1] + rng.gauss(0, 0.012))
    cand = evaluate_pair("A", "B", "Tech", la, lb, synthetic_pair=False)
    # Either reject outright, or yield a large ADF-like p-value.
    if cand is not None:
        assert cand.p_value > 0.30 or abs(cand.correlation) < 0.5


def test_screener_aborts_when_candidate_count_exceeds_guardrail():
    """A mis-configured single-sector universe with too many names produces
    O(N²) candidate pairs; the screener must refuse to run rather than
    silently exhaust the LLM budget."""
    from apps.portfolios.pairs import screen_pairs

    # 320 names in ONE sector → 320·319/2 = 51,040 > 50,000.
    members = [(f"T{i:04d}", "X") for i in range(320)]

    class _NoopProvider:
        def get_daily_bars(self, *_a, **_kw):
            return []

    with pytest.raises(RuntimeError, match="max_candidates_total"):
        screen_pairs(members, date.today(), data_provider=_NoopProvider(),
                     lookback_days=120, p_max=0.05, corr_min=0.7,
                     entry_z=2.0, max_candidates_total=50000)


def test_adf_like_p_low_for_stationary_series():
    # Pure AR(1) with ρ=0.5 is stationary.
    import random
    rng = random.Random(3)
    x = [0.0]
    for _ in range(400):
        x.append(0.5 * x[-1] + rng.gauss(0, 0.5))
    p = _adf_like_p(x)
    assert p < 0.15, f"stationary series got p={p}"
