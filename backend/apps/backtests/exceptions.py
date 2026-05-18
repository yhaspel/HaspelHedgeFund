"""Domain exceptions for the backtest pipeline."""
from __future__ import annotations


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
