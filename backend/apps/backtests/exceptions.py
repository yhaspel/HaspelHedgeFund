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
