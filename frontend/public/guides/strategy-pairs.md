# Pairs Trading (Cointegration)

**The goal of pairs trading is to earn a return stream that is largely independent of the overall market by trading the *gap* between two stocks that normally move together.** Use it when you want an "absolute return" sleeve uncorrelated with directional equity exposure — a piece that can profit whether the market rises or falls — and when you like the idea of statistics doing the picking while the AI council acts only as a sanity check.

## How it works

Some pairs of stocks move together for a real, structural reason: Coca-Cola and Pepsi share the soft-drinks market; Visa and Mastercard share card payments. The price **spread** between such a pair tends to oscillate around a stable average. This long-run tethering is called **cointegration**, and the app tests for it with the **Engle-Granger test**.

When the spread stretches unusually wide — measured by its **z-score**, how many standard deviations it sits from normal — the strategy **buys the laggard and short-sells the leader**, betting the gap will close again. It exits when the spread reverts to its average. Because both legs are in the same business, a broad market move lifts or sinks both — so the trade profits or loses almost entirely on the *relative* move between them.

Each pair has pre-set exit rules: close when the spread has reverted to normal, or **force-close** if it keeps widening past a "this is not reverting — get out" stop level. The two legs are always opened and closed **together**, so the book never carries a naked half-position.

## What the AI council does

This is the council's most surgical role of any strategy. The statistical screener does roughly 99% of the work — it finds the cointegrated candidate pairs. The council's single job is to **veto the traps**: cases where a spread has blown out for a *real reason* (one company missed earnings, lost a lawsuit, changed its CEO) and is therefore *not* going to revert. The council is a fundamentalist sanity check on a statistical process.

## Key settings

- **Universe** — the stocks from which candidate pairs are formed (usually restricted within sectors).
- **Entry threshold** — how stretched the spread must be (z-score) to open a pair.
- **Exit threshold** — how far the spread must revert to close a pair at a profit.
- **Stop threshold** — the z-score at which a non-reverting pair is force-closed.
- **Maximum open pairs** — how many pairs may run at once.
- **Cost ceiling per cycle** — the hard cap on AI spend.

## Best for / not ideal for

**Best for:** users who want a return stream uncorrelated with the market; a small "absolute return" sleeve to add alongside another strategy; anyone who likes statistics-led picking with the AI in a checking role.

**Not ideal for:** users who want to capture market upside (pairs trading is built to ignore it), or who want a single simple book — pairs is a more intricate strategy.

## What to watch

The core risk is a **structural break** — a relationship that has genuinely stopped holding, where the spread widens instead of reverting. That is exactly what the stop level and the council veto exist to catch, but neither is perfect. Pairs strategies are also notorious for looking better in testing than in reality, so honest **backtesting** matters here more than anywhere else.

## Status

Available as a strategy type in the New Strategy editor.
