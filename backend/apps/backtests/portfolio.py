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
    ) -> list[Fill]:
        """Execute decisions against `fill_prices` (e.g., next day's open).

        Each decision: {"ticker": str, "action": "buy"|"sell"|"hold",
                         "target_weight_pct": float, "target_quantity": float}.

        We compute target qty from current portfolio_value × target_weight%
        / fill_price (more robust than trusting upstream's target_quantity
        because the PM may have sized against a stale portfolio value).
        """
        self.fills_today = []
        equity = self.total_value  # snapshot before trades
        for d in decisions:
            tkr = d["ticker"]
            action = d.get("action", "hold")
            price = fill_prices.get(tkr)
            if price is None or price <= 0:
                continue
            target_weight = float(d.get("target_weight_pct", 0.0)) / 100.0
            target_dollars = equity * target_weight
            if action == "sell":
                target_qty = 0.0
            else:
                target_qty = target_dollars / price
            current = self.positions.get(tkr, Position(ticker=tkr)).qty
            delta = target_qty - current
            if abs(delta * price) < 1.0:  # ignore <$1 trades
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
