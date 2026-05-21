# The Agent Council

Every decision in the app is made by a **council** of AI agents working together. No single AI decides alone. This guide explains who the agents are and how they reach a verdict. Terms in **bold** are defined in the *Financial Terms & Abbreviations* guide.

## The idea

A real investment committee has specialists — analysts who measure things, seasoned investors who form judgements, a risk officer who says "no," and a portfolio manager who makes the final call. The council copies that structure. Each agent is a Large Language Model (**LLM**) given one clear job, one viewpoint, and the right inputs. They are run together, their outputs are combined, and disagreement is preserved rather than hidden.

## Who is on the council

### Persona agents — the investors

Each **persona** is modelled on a famous investor and carries that investor's philosophy:

- **Warren Buffett** — quality businesses with a durable **moat**, bought at a fair price.
- **Charlie Munger** — a mental-models sceptic who pressure-tests the Buffett view for flaws.
- **Benjamin Graham** — deep value and a strict **margin of safety**; the **net-net** bargain hunter.
- **Cathie Wood** — disruptive, high-growth innovation; large **TAM** and heavy R&D.
- **Stanley Druckenmiller** — macro direction plus price **momentum**; rides strong trends.
- **Michael Burry** — contrarian and short-biased; hunts for debt risk and accounting red flags.
- **Aswath Damodaran** — disciplined valuation; checks that the "story" and the numbers agree.
- **Peter Lynch** — "invest in what you know"; reasonable growth at a sensible price (**PEG**).

The personas deliberately disagree. A Buffett-style and a Cathie-Wood-style agent will rate the same stock very differently — that tension is the point.

### Analytical agents — the measurers

These agents compute objective inputs the personas rely on. They form fewer opinions and do more measuring:

- **Fundamentals agent** — pulls the financial statements and scores growth, profitability, and leverage.
- **Valuation agent** — runs valuation lenses (**DCF**, **multiples**, **residual income**) to estimate a **fair-value band**.
- **Technicals agent** — measures price-based signals: momentum, trend, mean-reversion, volatility.
- **Sentiment agent** — gauges the mood in news and commentary.

### Macro & news agents — the context

- **Macro agent** — maintains a view of the macro **regime** (growth, inflation, policy) and tags decisions with that context.
- **News agent** — scans filings (**8-K**, **10-Q**, **10-K**), earnings transcripts, and headlines for material events.

### Decision agents — the deciders

- **Risk Manager (RM)** — reviews every proposed position against hard limits (concentration, drawdown, exposure) and holds a **veto** over anything that breaches them.
- **Portfolio Manager (PM)** — the final decision-maker. Weighs every agent's **signal** by its **confidence**, applies the Risk Manager's constraints, and produces concrete trades.
- **CIO (Chief Investment Officer)** — a senior agent that sets high-level direction and oversight for the council.

## How a decision is made

1. **Gather inputs.** The analytical, macro, and news agents collect and measure the data.
2. **Debate.** Each persona studies the stock through its own philosophy and emits a **signal** (bullish / neutral / bearish), a **confidence** (0–100), a written **thesis**, and the key risks it sees.
3. **Check risk.** The Risk Manager reviews the proposed positions and vetoes any that break a hard rule.
4. **Decide.** The Portfolio Manager combines everything — confidence-weighted — into final trades, each with a rationale.
5. **Record dissent.** Minority views that disagreed with the outcome are kept and shown as **dissent**. The app never pretends the council was unanimous when it was not.

## How much the council varies by strategy

The council is not equally involved in every strategy. In stock-picking strategies (Long/Short, Concentrated Long-Only) it debates every name in depth. In mechanical strategies (Risk-Parity, Pairs Trading) most of the work is statistical and the council shrinks to a **veto-only** sanity check — there to catch traps, not to pick. Each strategy guide states the council's role for that strategy.

## Does the council actually help?

Not automatically. The app measures **council alpha** — the value the council adds over a simple mechanical baseline — and shows it per strategy. If council alpha is near zero, the AI layer is not earning its **cost**, and the honest move is to switch to a cheaper model **preset** or turn the council down. The app is built to let you find that out, not to assume the answer.
