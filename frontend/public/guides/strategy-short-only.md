# Short-Only

**The goal of a short-only strategy is to profit from decline: identify stocks you expect to fall, sell them short, and gain as their prices drop.** It is a specialist tool, not a starting point. Use it when you have a specific bearish thesis — a basket of overvalued or deteriorating companies — or when you want a dedicated piece that rises during market stress to offset long exposure held elsewhere.

## How it works

The strategy only ever **sells short** — it never buys. Short-selling means borrowing a stock, selling it now, and aiming to buy it back later at a lower price; the difference is the profit. The book makes money when its picks **fall** and loses when they rise.

Shorting carries costs and risks a long-only book does not. Every short pays a **borrow fee** for as long as it is held. A short can only be opened if the broker can **locate** shares to lend. And the risk is asymmetric: a stock you are long can only fall to zero, but a stock you are short can rise without limit — so losses on a short are, in principle, unbounded.

## What the AI council does

The council runs in reverse of its usual mode: it is hunting for weakness. The persona agents — Michael Burry's contrarian, debt-focused lens especially — and the analytical agents look for overvaluation, deteriorating fundamentals, debt risk, and accounting red flags. The Risk Manager pays particular attention here, because short losses can run.

## Key settings

- **Universe** — the stocks the strategy may short.
- **Maximum position** — the cap on any single short.
- **Number of holdings** — how many shorts to spread across.
- **Cost ceiling per cycle** — the hard cap on AI spend.

## Best for / not ideal for

**Best for:** experienced users with a specific bearish view; a deliberate hedge against long exposure held in another strategy.

**Not ideal for:** beginners, and anyone uneasy with uncapped downside. Markets rise more often than they fall, so a standalone short book swims against the long-run tide.

## What to watch

Borrow fees quietly erode returns the longer positions stay open. A short that moves against you grows *larger* as it loses — the opposite of a long — so position discipline and stops matter more here than anywhere else.

## Status

Available as a strategy type in the New Strategy editor.
