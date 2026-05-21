"""Markov Regime Classifier (P2m).

Deterministic, price-based regime detector used as a feature by other
strategies. Two model variants:

* ``labelled_markov`` (default, production) — label each trailing-W-day
  return as bull/sideways/bear by a ±threshold, then build the 3x3
  transition matrix with Laplace smoothing. Pure NumPy, milliseconds to
  fit, fully auditable. **This is not a "real" Markov chain in the
  conditional-independence sense — the labels are deterministic functions
  of trailing prices, so the transition matrix is mechanical bookkeeping.**
  It earns its keep as a cheap, deterministic counter-signal to the LLM
  Macro agent, not as a learned process model.
* ``gaussian_hmm`` — optional research add-on. A 3-state Gaussian HMM on
  log-returns; hidden states are labelled by ascending mean (lowest →
  bear, highest → bull). EM seeded from a stable SHA so two processes get
  the same model for the same (ticker, as_of). Off by default.

Walk-forward by construction: the fit on ``as_of_date = D`` only sees
bars whose ``date < D``. ``training_end_date`` records the latest bar
actually used.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


STATES = ("bear", "sideways", "bull")
STATE_INDEX = {s: i for i, s in enumerate(STATES)}


class InsufficientHistoryError(RuntimeError):
    """Not enough usable bars before as_of_date to fit a model."""


class UndertrainedStateError(RuntimeError):
    """A label has fewer than the minimum required observations.

    The transition row for that state would be a Laplace-only prior, which
    is degenerate. Refusing to emit is safer than silently calling it
    neutral.
    """


# ---------------------------------------------------------------------------
# Config + dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarkovConfig:
    model_type: str = "labelled_markov"
    return_window_days: int = 20
    bull_threshold: float = 0.05
    bear_threshold: float = -0.05
    price_field: str = "adjusted_close"
    fit_lookback_observations: int = 2520
    min_observations_per_state: int = 5
    min_fit_fraction: float = 0.6
    laplace_alpha: float = 1.0

    def hash(self) -> str:
        """Stable SHA-256 prefix that's unique per config; lives on RegimeModel rows."""
        blob = json.dumps(
            {
                "model_type": self.model_type,
                "W": self.return_window_days,
                "bull": self.bull_threshold,
                "bear": self.bear_threshold,
                "price_field": self.price_field,
                "L": self.fit_lookback_observations,
                "min_per_state": self.min_observations_per_state,
                "alpha": self.laplace_alpha,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class RegimeFit:
    ticker: str
    as_of_date: dt.date
    config: MarkovConfig
    transition_matrix: list[list[float]]
    state_labels: dict[str, str]
    stationary_distribution: dict[str, float]
    current_state: str
    current_return: float | None
    last_price_date: dt.date
    training_start_date: dt.date
    training_end_date: dt.date
    fit_observations: int
    observations_available: int
    state_counts: dict[str, int] = field(default_factory=dict)
    state_means: list[float] | None = None
    state_stds: list[float] | None = None
    log_likelihood: float | None = None
    fit_metadata: dict[str, Any] = field(default_factory=dict)

    def forecast(self, n_steps: int) -> dict[str, float]:
        """Distribution over states n_steps ahead starting from current_state."""
        return _forecast(self.transition_matrix, self.current_state, n_steps)

    @property
    def current_state_persistence(self) -> float:
        i = STATE_INDEX[self.current_state]
        return float(self.transition_matrix[i][i])

    @property
    def bull_persistence(self) -> float:
        i = STATE_INDEX["bull"]
        return float(self.transition_matrix[i][i])

    @property
    def sideways_persistence(self) -> float:
        i = STATE_INDEX["sideways"]
        return float(self.transition_matrix[i][i])

    @property
    def bear_persistence(self) -> float:
        i = STATE_INDEX["bear"]
        return float(self.transition_matrix[i][i])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar_price(bar: Any, field_name: str) -> float | None:
    """Pull adjusted_close (or configured field) from a Bar dataclass or dict."""
    value = getattr(bar, field_name, None)
    if value is None and isinstance(bar, dict):
        value = bar.get(field_name)
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _bar_date(bar: Any) -> dt.date | None:
    d = getattr(bar, "date", None)
    if d is None and isinstance(bar, dict):
        d = bar.get("date")
    if isinstance(d, str):
        return dt.date.fromisoformat(d)
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    return None


def _label(r: float, bull_thr: float, bear_thr: float) -> str:
    if r >= bull_thr:
        return "bull"
    if r <= bear_thr:
        return "bear"
    return "sideways"


def _stationary(P: np.ndarray) -> np.ndarray:
    """Compute the stationary distribution from a row-stochastic matrix.

    Solves for the left-eigenvector with eigenvalue 1 and falls back to a
    uniform distribution if the eigendecomposition is numerically unhappy
    (only realistic when P is degenerate — but Laplace smoothing makes that
    practically impossible).
    """
    eigvals, eigvecs = np.linalg.eig(P.T)
    idx = np.argmin(np.abs(eigvals - 1.0))
    vec = np.real(eigvecs[:, idx])
    if vec.sum() == 0:
        return np.full(P.shape[0], 1.0 / P.shape[0])
    vec = np.abs(vec)
    return vec / vec.sum()


def _forecast(P: list[list[float]], current_state: str, n_steps: int) -> dict[str, float]:
    e = np.zeros(3)
    e[STATE_INDEX[current_state]] = 1.0
    M = np.asarray(P, dtype=float)
    if n_steps <= 0:
        out = e
    else:
        out = e @ np.linalg.matrix_power(M, int(n_steps))
    return {STATES[i]: float(out[i]) for i in range(3)}


# ---------------------------------------------------------------------------
# Labelled Markov fitter
# ---------------------------------------------------------------------------

def fit_labelled_markov(
    *,
    ticker: str,
    as_of_date: dt.date,
    bars: list[Any],
    config: MarkovConfig | None = None,
) -> RegimeFit:
    """Fit a labelled-Markov regime model for ``ticker`` as of ``as_of_date``.

    ``bars`` is the full daily-bar history; this routine drops bars with
    ``date >= as_of_date`` and keeps the most recent
    ``fit_lookback_observations + return_window_days`` so the first
    labelled return has W prior closes.
    """
    cfg = config or MarkovConfig()
    if cfg.model_type != "labelled_markov":
        raise ValueError(f"fit_labelled_markov called with model_type={cfg.model_type!r}")

    # Keep only bars strictly before as_of_date and sort ascending.
    usable = [b for b in bars if _bar_date(b) is not None and _bar_date(b) < as_of_date]
    usable.sort(key=_bar_date)
    if not usable:
        raise InsufficientHistoryError(
            f"{ticker}: no bars before as_of={as_of_date.isoformat()}"
        )

    # Limit to the requested lookback (plus the window so the first labelled
    # day has W prior closes).
    window = max(2, int(cfg.return_window_days))
    keep = max(1, int(cfg.fit_lookback_observations) + window)
    usable = usable[-keep:]

    closes = [(_bar_date(b), _bar_price(b, cfg.price_field)) for b in usable]
    closes = [(d, p) for d, p in closes if d is not None and p is not None and p > 0]
    if len(closes) < window + 2:
        raise InsufficientHistoryError(
            f"{ticker}: only {len(closes)} usable bars (need > {window + 1}) "
            f"before as_of={as_of_date.isoformat()}"
        )

    observations_available = len(closes)
    dates = [d for d, _ in closes]
    prices = np.array([p for _, p in closes], dtype=float)

    # Labelled-return series: returns[t] = price[t]/price[t-W] - 1 for t >= W.
    returns = prices[window:] / prices[:-window] - 1.0
    labels = np.array(
        [_label(float(r), cfg.bull_threshold, cfg.bear_threshold) for r in returns]
    )
    fit_observations = int(labels.size)
    if fit_observations < 2:
        raise InsufficientHistoryError(
            f"{ticker}: only {fit_observations} labelled returns (need >= 2)"
        )

    required = int(cfg.min_fit_fraction * cfg.fit_lookback_observations)
    if fit_observations < required:
        raise InsufficientHistoryError(
            f"{ticker}: {fit_observations} labelled observations < required {required}"
        )

    # State-count guardrail: any state with < min_observations_per_state has an
    # undertrained transition row even after Laplace smoothing.
    state_counts = {s: int(np.sum(labels == s)) for s in STATES}
    starved = [s for s, c in state_counts.items() if c < cfg.min_observations_per_state]
    if starved:
        raise UndertrainedStateError(
            f"{ticker}: states {starved} have < {cfg.min_observations_per_state} "
            f"observations (counts={state_counts})"
        )

    # Build the 3x3 transition count matrix C with Laplace smoothing.
    C = np.full((3, 3), float(cfg.laplace_alpha), dtype=float)
    for prev, nxt in zip(labels[:-1], labels[1:], strict=True):
        C[STATE_INDEX[prev], STATE_INDEX[nxt]] += 1.0
    row_sums = C.sum(axis=1, keepdims=True)
    P = C / row_sums

    pi = _stationary(P)
    stationary = {STATES[i]: float(pi[i]) for i in range(3)}

    training_end_date = dates[-1]
    training_start_date = dates[0]
    current_state = str(labels[-1])
    current_return = float(returns[-1])

    fit = RegimeFit(
        ticker=ticker,
        as_of_date=as_of_date,
        config=cfg,
        transition_matrix=[[float(v) for v in row] for row in P],
        state_labels={str(i): STATES[i] for i in range(3)},
        stationary_distribution=stationary,
        current_state=current_state,
        current_return=current_return,
        last_price_date=training_end_date,
        training_start_date=training_start_date,
        training_end_date=training_end_date,
        fit_observations=fit_observations,
        observations_available=observations_available,
        state_counts=state_counts,
        fit_metadata={
            "laplace_alpha": cfg.laplace_alpha,
            "labels_tail": [str(x) for x in labels[-5:].tolist()],
        },
    )
    return fit


# ---------------------------------------------------------------------------
# Gaussian HMM fitter (optional research add-on)
# ---------------------------------------------------------------------------

def _hmm_seed(ticker: str, as_of_date: dt.date) -> int:
    digest = hashlib.sha256(f"hmm|{ticker}|{as_of_date.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def fit_gaussian_hmm(
    *,
    ticker: str,
    as_of_date: dt.date,
    bars: list[Any],
    config: MarkovConfig | None = None,
) -> RegimeFit:
    """Optional Gaussian-HMM variant. Requires hmmlearn to be installed.

    The HMM provides the structure; labels are *assigned* deterministically
    by ordering hidden states by mean log-return (lowest → bear, highest →
    bull). Seeded by a SHA-derived integer so two processes get the same
    model for the same (ticker, as_of_date).
    """
    cfg = config or MarkovConfig(model_type="gaussian_hmm")
    try:
        from hmmlearn import hmm  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only when extra installed
        raise RuntimeError(
            "Gaussian HMM regime classifier requires `hmmlearn`. "
            "Install it as a backend extra or run with model_type='labelled_markov'."
        ) from exc

    usable = [b for b in bars if _bar_date(b) is not None and _bar_date(b) < as_of_date]
    usable.sort(key=_bar_date)
    closes = [(_bar_date(b), _bar_price(b, cfg.price_field)) for b in usable]
    closes = [(d, p) for d, p in closes if d is not None and p is not None and p > 0]
    closes = closes[-int(cfg.fit_lookback_observations) - 1 :]
    if len(closes) < 60:
        raise InsufficientHistoryError(
            f"{ticker}: only {len(closes)} usable bars for HMM"
        )
    observations_available = len(closes)
    dates = [d for d, _ in closes]
    prices = np.array([p for _, p in closes], dtype=float)
    log_returns = np.log(prices[1:] / prices[:-1])
    fit_observations = int(log_returns.size)

    model = hmm.GaussianHMM(
        n_components=3,
        covariance_type="diag",
        random_state=_hmm_seed(ticker, as_of_date),
        n_iter=200,
        tol=1e-3,
    )
    X = log_returns.reshape(-1, 1)
    model.fit(X)
    # Decode the most likely state sequence over the training window.
    hidden = model.predict(X)
    # Order states by mean ascending → bear, sideways, bull.
    means = model.means_.reshape(-1)
    order = np.argsort(means)
    label_for = {int(order[0]): "bear", int(order[1]): "sideways", int(order[2]): "bull"}

    # Reorder the transition matrix to (bear, sideways, bull) ordering.
    P = np.zeros((3, 3), dtype=float)
    for i_old in range(3):
        for j_old in range(3):
            i_new = STATE_INDEX[label_for[int(i_old)]]
            j_new = STATE_INDEX[label_for[int(j_old)]]
            P[i_new, j_new] = float(model.transmat_[i_old, j_old])
    # Normalise rows just in case hmmlearn returned not-quite-stochastic rows.
    P = P / P.sum(axis=1, keepdims=True)
    pi = _stationary(P)

    current_state = label_for[int(hidden[-1])]
    # Re-order means / stds into bear/sideways/bull order.
    stds = np.sqrt(model.covars_.reshape(-1))
    ordered_means = [float(model.means_[order[0]][0]),
                     float(model.means_[order[1]][0]),
                     float(model.means_[order[2]][0])]
    ordered_stds = [float(stds[order[0]]),
                    float(stds[order[1]]),
                    float(stds[order[2]])]

    return RegimeFit(
        ticker=ticker,
        as_of_date=as_of_date,
        config=cfg,
        transition_matrix=[[float(v) for v in row] for row in P],
        state_labels={str(i): STATES[i] for i in range(3)},
        stationary_distribution={STATES[i]: float(pi[i]) for i in range(3)},
        current_state=current_state,
        current_return=float(log_returns[-1]),
        last_price_date=dates[-1],
        training_start_date=dates[0],
        training_end_date=dates[-1],
        fit_observations=fit_observations,
        observations_available=observations_available,
        state_means=ordered_means,
        state_stds=ordered_stds,
        log_likelihood=float(model.score(X)),
        fit_metadata={
            "seed": _hmm_seed(ticker, as_of_date),
            "n_iter": int(getattr(model.monitor_, "iter", 0) or 0),
            "converged": bool(model.monitor_.converged),
        },
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fit_regime(
    *,
    ticker: str,
    as_of_date: dt.date,
    bars: list[Any],
    config: MarkovConfig | None = None,
) -> RegimeFit:
    """Dispatch to the labelled or HMM fitter based on config.model_type."""
    cfg = config or MarkovConfig()
    if cfg.model_type == "gaussian_hmm":
        return fit_gaussian_hmm(
            ticker=ticker, as_of_date=as_of_date, bars=bars, config=cfg
        )
    return fit_labelled_markov(
        ticker=ticker, as_of_date=as_of_date, bars=bars, config=cfg
    )
