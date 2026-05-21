# Risk-Parity / Multi-Asset Lite

**The goal of risk parity is genuine diversification: build a multi-asset book — stocks, bonds, gold — where each piece contributes a *similar amount of risk*, rather than a similar number of dollars.** Use it when you want a calm, diversified portfolio that does not depend on the council being right about anything in particular, when you prefer low turnover, and when you want a deliberately mechanical baseline to compare the more AI-heavy strategies against.

## How it works

Risk parity starts from an uncomfortable fact: in a typical 60%-stocks / 40%-bonds portfolio, stock volatility dominates total risk — because stocks are roughly three times as volatile as bonds, "60/40 by dollars" is closer to "90/10 by risk." Risk parity fixes that by balancing the **risk**, not the dollars.

The strategy holds several **sleeves** — sector-equity ETFs plus bond-proxy ETFs, and optionally gold — and sizes each one by **inverse-volatility weighting**: the calmer an asset, the larger its dollar slice; the jumpier, the smaller. Each sleeve then contributes a comparable share of the book's ups and downs, so no single one drives the ride. It **rebalances** only when weights have drifted enough to be worth the trading cost, which keeps turnover low.

## What the AI council does

Less than in any other strategy — and that is intentional. The allocation is **mechanical**: an inverse-volatility calculation produces the weights. The council's role shrinks to a **veto vote** — should any one sleeve be excluded this cycle (for example, "this sector is in a credit freeze; skip it")? Risk-parity exists partly to demonstrate that not every problem needs an LLM.

## Key settings

- **Sleeve universe** — the sector-equity and bond-proxy ETFs (and optional gold) to include.
- **Volatility window** — the look-back period used to measure each sleeve's volatility.
- **Rebalance band** — how far weights may drift before a rebalance is triggered.
- **Council veto** — whether the council may exclude a sleeve; can be turned off entirely for a fully deterministic book.
- **Cost ceiling per cycle** — the hard cap on AI spend.

## Best for / not ideal for

**Best for:** users who want a "set and forget", well-diversified book; those who trust diversification over forecasting; anyone who wants a cheap, mechanical benchmark to measure the other strategies against.

**Not ideal for:** users seeking high returns from sharp calls — risk parity is built for steadiness, not for big swings.

## What to watch

Returns are deliberately modest and slow — this is ballast, not a rocket. With the council veto switched off it is the cheapest strategy to run, which makes it the ideal control: if an LLM-heavy strategy cannot beat plain inverse-volatility weighting, that is worth knowing.

## Status

Available as a strategy type in the New Strategy editor.
