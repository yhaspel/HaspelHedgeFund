# Long/Short

**The goal of a long/short strategy is to profit from being right about *relative* winners and losers, with less dependence on the market's overall direction.** It is the classic hedge-fund recipe. Use it when you want your returns to come from stock selection rather than from a rising tide, and when you want a book that holds up better than a long-only one when the market falls — because the short side works in your favour exactly when the long side hurts.

## How it works

The strategy does two things at once: it **buys** a set of stocks it expects to outperform (the long side) and **short-sells** a set it expects to underperform (the short side). Since most stocks move partly with the market, the two sides partially cancel — so the book's profit comes mainly from the **gap** between the longs and the shorts, not from the market's direction itself.

You control how the book leans through its **net exposure** — longs minus shorts. A book tilted net-long still benefits from a rising market; a balanced one is closer to neutral. The short side also carries borrow fees and locate requirements (see the Short-Only guide).

## What the AI council does

This is the council's fullest workout. Every candidate is debated in depth: the personas and analytical agents argue both directions — what deserves to be owned and what deserves to be shorted. The Portfolio Manager then builds a two-sided book, and the Risk Manager keeps either side from becoming too concentrated.

## Key settings

- **Universe** — the stocks available for both sides.
- **Target net exposure** — how far the book is allowed to lean long or short overall.
- **Number of longs / number of shorts** — how many names on each side.
- **Maximum position** — the single-name cap.
- **Cost ceiling per cycle** — the hard cap on AI spend.

## Best for / not ideal for

**Best for:** users who want a genuine hedge-fund-style book; those who believe the council can tell good companies from bad and want that skill — not market direction — to drive returns.

**Not ideal for:** users who want the simplest possible setup (start with Long-Only), or who want market direction stripped out entirely (use Market-Neutral).

## What to watch

Running two sides costs more than one — more positions, more council debate, plus borrow fees. The payoff only arrives if the longs genuinely beat the shorts; if both sides are merely average, the costs still apply. Watch whether the council's selection is actually creating a gap.

## Status

Available as a strategy type in the New Strategy editor.
