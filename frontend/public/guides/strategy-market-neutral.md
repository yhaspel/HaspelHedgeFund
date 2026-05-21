# Market-Neutral

**The goal of a market-neutral strategy is to earn returns purely from stock selection, with the market's direction removed almost entirely.** Use it when you want a steady, low-swing return stream that does not depend on guessing whether the market goes up or down — and when you want a real cushion in a crash. It is the strategy for someone who wants their results to be a clean verdict on the council's stock-picking skill, and nothing else.

## How it works

A market-neutral book is a long/short book with two extra constraints applied deliberately:

- **Dollar-neutral** — roughly equal dollars are invested long and short, so the two sides offset.
- **Beta-neutral** — the holdings are chosen and sized so the book's overall sensitivity to the market (**beta**) is close to zero.

The result: if the whole market jumps or drops 5%, the book is built to barely move. Its profit and loss come almost entirely from one thing — whether the chosen longs beat the chosen shorts. Returns are typically smaller than a directional book's, but far steadier, and the book does not collapse when the market does.

## What the AI council does

The same full per-name debate as Long/Short — the personas and analytical agents argue both sides for every candidate. The difference is in construction: the Portfolio Manager must size the book to satisfy the dollar- and beta-neutral constraints, which limits how freely it can lean toward its favourite ideas.

## Key settings

- **Universe** — the stocks available for both sides.
- **Neutrality constraints** — the dollar- and beta-neutral targets the construction must respect.
- **Number of longs / number of shorts** — names per side.
- **Maximum position** — the single-name cap.
- **Cost ceiling per cycle** — the hard cap on AI spend.

## Best for / not ideal for

**Best for:** users who want low-volatility, crash-resistant returns; anyone who wants a pure test of selection skill; a stabilising sleeve alongside more directional strategies.

**Not ideal for:** users who want to capture a rising market — neutrality removes that upside along with the downside.

## What to watch

Neutrality cuts both ways: it removes the market's help as well as its harm. If the council's longs and shorts are equally good, the book grinds sideways — and still pays the cost of running two sides. The honest question is whether the selection produces a genuine, repeatable gap.

## Status

Available as a strategy type in the New Strategy editor.
