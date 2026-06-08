"""SimulatedPortfolio for backtests.

Holds cash + positions, marks to market against a price provider, applies
corporate actions, and executes a list of decisions with configurable
commission_bps + spread_bps.

All quantities are floats; this is sim, not bookkeeping.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Position:
    ticker: str
    qty: float = 0.0
    avg_cost: float = 0.0
    mark: float = 0.0

    @property
    def market_value(self) -> float:
        return self.qty * self.mark

    def snapshot(self) -> dict:
        return {
            "ticker": self.ticker,
            "qty": self.qty,
            "avg_cost": self.avg_cost,
            "mark": self.mark,
            "market_value": self.market_value,
        }


@dataclass
class Fill:
    ticker: str
    qty: float                # signed: +=buy, -=sell
    price: float              # execution price (incl spread)
    commission: float
    notional: float           # qty * price (signed)
    cash_delta: float         # cash impact (signed)


@dataclass
class SimulatedPortfolio:
    starting_cash: float
    commission_bps: float = 5.0
    spread_bps: float = 5.0
    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    fills_today: list[Fill] = field(default_factory=list)
    turnover_total_notional: float = 0.0  # cumulative |notional|, for turnover metric

    def __post_init__(self) -> None:
        if self.cash == 0.0:
            self.cash = float(self.starting_cash)

    @property
    def total_value(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    # ---- pricing -----------------------------------------------------

    def mark_to_market(self, prices: dict[str, float]) -> None:
        """Update each position's mark from a {ticker: price} map.
        Missing tickers keep their previous mark (e.g., holiday)."""
        for tkr, p in self.positions.items():
            new_mark = prices.get(tkr)
            if new_mark is not None and new_mark > 0:
                p.mark = float(new_mark)

    # ---- corporate actions ------------------------------------------

    def apply_split(self, ticker: str, ratio: float) -> None:
        """E.g., 2:1 split => ratio=2.0: qty doubles, avg_cost halves."""
        p = self.positions.get(ticker)
        if not p or p.qty == 0:
            return
        p.qty *= ratio
        p.avg_cost /= ratio
        if p.mark:
            p.mark /= ratio

    def apply_dividend(self, ticker: str, dividend_per_share: float) -> None:
        p = self.positions.get(ticker)
        if not p or p.qty == 0:
            return
        self.cash += p.qty * float(dividend_per_share)

    def apply_merger_cash(self, ticker: str, cash_per_share: float) -> None:
        p = self.positions.get(ticker)
        if not p or p.qty == 0:
            return
        self.cash += p.qty * float(cash_per_share)
        p.qty = 0.0
        p.avg_cost = 0.0

    # ---- execution --------------------------------------------------

    def execute(
        self,
        decisions: list[dict],
        fill_prices: dict[str, float],
        as_of: dt.date | None = None,
        hold_semantics: str = "hold_existing",
        max_gross: float = 1.0,
    ) -> list[Fill]:
        """Execute decisions against `fill_prices` (e.g., next day's open).

        Each decision: {"ticker": str, "action": "buy"|"sell"|"hold"|"open_short",
                         "target_weight_pct": float (signed), "target_quantity": float}.

        `hold_semantics`:
          - "hold_existing" — action="hold" skips the name (keep current target).
          - "target_zero"   — action="hold" liquidates (target_weight_pct treated as 0).
        Both behaviors are reachable; tests pin one per backtest.

        We compute target qty from current portfolio_value × target_weight%
        / fill_price (more robust than trusting upstream's target_quantity
        because the PM may have sized against a stale portfolio value).

        Risk guards (leverage hardening): the PM emits per-name weights that are
        NOT normalized to a gross budget, so this is the only aggregate control.
          - `max_gross` caps the resulting book's gross exposure (Σ|market_value|)
            at `max_gross × equity`; actively-targeted weights are scaled down
            pro-rata if they would breach it. Default 1.0 = 100% gross, no leverage.
          - cash is floored at 0: a buy is clipped to available cash rather than
            borrowing implicitly. Reductions are executed before increases so
            position rotations are funded by the same day's sells.
        These guards are no-ops when the intended book is already within budget
        (the historical case), so they do not change prior results.
        """
        self.fills_today = []
        equity = self.total_value  # snapshot before trades

        # Pass 1: resolve a signed target qty for each actionable decision.
        targets: dict[str, float] = {}
        for d in decisions:
            tkr = d["ticker"]
            action = d.get("action", "hold")
            price = fill_prices.get(tkr)
            if price is None or price <= 0:
                continue
            if action == "hold" and hold_semantics == "hold_existing":
                continue
            target_weight = float(d.get("target_weight_pct", 0.0)) / 100.0
            if action == "sell" or (action == "hold" and hold_semantics == "target_zero"):
                targets[tkr] = 0.0
            else:
                targets[tkr] = (equity * target_weight) / price

        # Pass 2: cap gross exposure of the resulting book at max_gross × equity.
        # Positions we are not re-targeting this cycle keep their size and count
        # against the budget.
        if max_gross and max_gross > 0 and equity > 0:
            held_gross = sum(
                abs(p.qty * (fill_prices.get(t) or p.mark))
                for t, p in self.positions.items()
                if t not in targets and p.qty
            )
            target_gross = sum(abs(q * fill_prices[t]) for t, q in targets.items())
            budget = max_gross * equity - held_gross
            if target_gross > budget and target_gross > 0:
                scale = max(0.0, budget) / target_gross
                targets = {t: q * scale for t, q in targets.items()}

        # Pass 3: reductions (cash-freeing) before increases (cash-using), with a
        # cash floor at the margin allowance so leverage cannot exceed max_gross.
        # max_gross<=1.0 → floor 0 (no borrowing, the original behavior); max_gross>1.0
        # lets a buy borrow down to -(max_gross-1)×equity (intentional leverage).
        cash_floor = -max(0.0, max_gross - 1.0) * equity

        def _delta(t: str, q: float) -> float:
            return q - self.positions.get(t, Position(ticker=t)).qty

        items = sorted(
            targets.items(), key=lambda kv: _delta(kv[0], kv[1]) * fill_prices[kv[0]]
        )
        for tkr, target_qty in items:
            price = fill_prices[tkr]
            delta = _delta(tkr, target_qty)
            notional = delta * price
            if notional > 0 and self.cash - notional < cash_floor:
                # would breach the margin allowance: clip the buy to what's affordable
                delta = max(0.0, self.cash - cash_floor) / price
                notional = delta * price
            if abs(notional) < 1.0:  # ignore <$1 trades
                continue
            fill = self._fill(tkr, delta, price)
            self.fills_today.append(fill)
        return self.fills_today

    def _fill(self, ticker: str, qty_delta: float, mid_price: float) -> Fill:
        side = 1 if qty_delta > 0 else -1
        # half-spread on entry/exit
        exec_price = mid_price * (1.0 + side * self.spread_bps / 2.0 / 10_000.0)
        notional = qty_delta * exec_price
        commission = abs(notional) * (self.commission_bps / 10_000.0)
        cash_delta = -notional - commission
        self.cash += cash_delta
        p = self.positions.setdefault(ticker, Position(ticker=ticker))
        new_qty = p.qty + qty_delta
        if (p.qty >= 0 and qty_delta > 0) or (p.qty <= 0 and qty_delta < 0):
            # adding to same side: update avg cost
            if (p.qty + qty_delta) != 0:
                p.avg_cost = (p.avg_cost * p.qty + exec_price * qty_delta) / (p.qty + qty_delta)
        elif abs(qty_delta) >= abs(p.qty):
            # crossed through zero
            p.avg_cost = exec_price if new_qty != 0 else 0.0
        # else: trimming; avg_cost unchanged
        p.qty = new_qty
        if abs(p.qty) < 1e-9:
            p.qty = 0.0
            p.avg_cost = 0.0
        p.mark = mid_price
        self.turnover_total_notional += abs(notional)
        return Fill(
            ticker=ticker, qty=qty_delta, price=exec_price,
            commission=commission, notional=notional, cash_delta=cash_delta,
        )

    # ---- snapshot ---------------------------------------------------

    def snapshot(self) -> list[dict]:
        return [p.snapshot() for p in self.positions.values() if p.qty != 0]

    def as_decimal(self, x: float) -> Decimal:
        return Decimal(str(round(x, 2)))
