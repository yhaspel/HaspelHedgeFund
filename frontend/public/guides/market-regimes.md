# Market Regimes & the Markov Classifier

Markets behave differently at different times. A calm, steadily rising market is not the same environment as a panicky, falling one — and a strategy that thrives in one can struggle in the other. The app detects which environment it is in. That environment is called a **regime**, and this guide explains what regimes are and how the app finds them.

## What a regime is

A **regime** is the prevailing character of the market. The app works with three:

- **Bull** — prices trending up, volatility low, conditions calm.
- **Sideways** — prices range-bound, no clear direction.
- **Bear** — prices falling, volatility high, conditions stressed.

The catch: the regime is never labelled for you in real time. You only ever see prices and how much they jump around — the regime itself is *hidden* and must be inferred. That inference is exactly what the regime classifier does.

## Markov models, in plain language

A **Markov process** is a model with one simplifying assumption: *the next state depends only on the current state, not on the whole history that came before.* Tomorrow's regime depends on today's regime — not on what happened last year. That assumption keeps the maths tractable while still capturing the key behaviour: regimes tend to **persist** (a bull market usually stays a bull market for a while) and then **transition** (occasionally it flips to bear).

A **Hidden Markov Model (HMM)** adds the realistic twist that you cannot see the state directly. You see only the *observations* — daily returns and volatility — and the model works backward to estimate the hidden regime most likely to have produced them. "Hidden" is the key word: the regime is a hypothesis the model fits, not a fact it reads off.

## How the app classifies regimes

The app's classifier looks at recent price behaviour for a set of market benchmarks (broad-market and sector ETFs) and, for each one, estimates the most likely current regime. It produces:

- a **state** for each benchmark — bull, sideways, or bear;
- a measure of how strongly the market leans bullish versus bearish;
- a **persistence** estimate — how likely the current regime is to hold rather than flip;
- a **consensus** across all the benchmarks — the regime most of them agree on, and how strong that agreement is.

It uses a straightforward labelled-Markov method as its primary approach, with a Gaussian HMM available as an alternative. Both answer the same question — *which hidden regime are we most likely in?*

## How strategies use the regime

The regime signal is an *input*, never an autopilot. Strategies can opt in to use it:

- **Exposure scaling.** A strategy can dial its market exposure down in a bear regime and back up in a bull regime — staying invested but smaller when conditions are hostile. Floor and ceiling settings keep the scaling within bounds you choose.
- **A regime gate.** Some strategies (such as Global Macro and Risk-Parity) can use a high probability of a bear regime as a trigger to act more defensively.

Each strategy guide notes whether and how that strategy responds to the regime. By default, regime-based adjustment is **off** — you switch it on per strategy.

## What to keep in mind

- **The regime is an estimate.** It is the model's best guess from noisy data, and it can be wrong, especially right at a turning point.
- **It lags at turns.** The classifier needs a little new data to recognise that a regime has changed, so it tends to confirm a turn slightly after it began.
- **It is a tool, not a crystal ball.** Use it to make a strategy more robust, not to try to perfectly time the market.

## Status

The regime classifier is built and available. Strategies can opt in to regime-based adjustment from the New Strategy editor; by default it is off.
