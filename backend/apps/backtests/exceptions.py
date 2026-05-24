"""Domain exceptions for the backtest pipeline."""
from __future__ import annotations


class SparseCache(RuntimeError):
    """Raised by prime_agent_cache when too many (ticker, day) invocations failed.

    Walk-forward against a sparse cache produces metrics whose decisions are
    silently dropped where the cache misses. We surface this as a first-class
    `ABORTED_PARTIAL` state instead of charting incomplete results.
    """

    def __init__(self, completeness: float, required: float, done: int, total: int) -> None:
        super().__init__(
            f"prime_agent_cache: completeness {completeness:.2%} below required "
            f"{required:.2%} ({done}/{total} invocations)"
        )
        self.completeness = completeness
        self.required = required
        self.done = done
        self.total = total


class BudgetExceeded(RuntimeError):
    """Raised by prime_agent_cache when total_cost_usd reaches max_budget_usd."""

    def __init__(self, spent: float, cap: float, done: int, total: int) -> None:
        super().__init__(
            f"Budget cap reached: spent ${spent:.2f} >= cap ${cap:.2f} "
            f"after {done}/{total} agent invocations."
        )
        self.spent = spent
        self.cap = cap
        self.done = done
        self.total = total


class ModelUnavailable(RuntimeError):
    """Raised by an LLM adapter on a non-retriable, non-transient error that
    will repeat on every subsequent call to the same model: HTTP 401/402/403/404.

    The canonical case (and the one we got burned by) is HTTP 402 from an
    OpenRouter "free" route whose upstream provider has run out of pre-funded
    credits ("Out of credits. Top up at /dashboard/billing to continue."). The
    adapter's exponential-backoff retry is designed for transient failures
    (429/5xx); it does nothing for a permanently-broken model and just delays
    the eventual run-killing error by many seconds.

    prime_agent_cache catches this once, then re-raises so the backtest aborts
    immediately with a clear config-error status instead of silently failing
    every ticker-day and eventually hitting prime_min_completeness.
    """

    def __init__(self, model: str, status_code: int, body: str) -> None:
        super().__init__(
            f"Model {model!r} is unavailable (HTTP {status_code}): "
            f"{body[:300]}"
        )
        self.model = model
        self.status_code = status_code
        self.body = body
