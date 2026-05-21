# Models & Cost Disclaimer

> [!WARNING]
> **Please read this before running anything.** Runs, strategy cycles, and backtests spend real money on AI usage — charged to *your* provider account through the keys you connected. There is no spending cap built into your provider account by default. Costs are easy to underestimate, especially for backtests.

## Why there is a cost at all

Every agent in the council is a Large Language Model (LLM). LLMs are billed by the **token** — a token is a chunk of text, very roughly three-quarters of a word. You pay for the tokens the app sends *in* (the prompt: prices, financials, instructions) and the tokens the model sends *out* (its reasoning and answer). Premium models cost many times more per token than budget models.

A single Run with a full council makes **many** model calls — one or more per agent. A strategy **cycle** does the same, repeatedly, on a schedule. A **backtest** does it again for *every step* in the historical window. The cost of a backtest is therefore roughly:

> **window length × number of stocks × number of agents × model price**

This multiplies fast. A long backtest over a large universe on a premium model is the most expensive action in the app — it can cost orders of magnitude more than a single Run.

## The models the app uses

The app is multi-model by design. Agents can run on:

- **Frontier closed models** — e.g. Claude Sonnet and Claude Opus. Highest quality, highest price. Best for the decision-making agents (personas, Risk Manager, Portfolio Manager).
- **Fast, cheap closed models** — e.g. Claude Haiku. Inexpensive; fine for the mechanical analytical agents.
- **Hosted open-weight models** — e.g. Qwen and Llama, via OpenRouter. Very cheap; the budget default.
- **Local models** — open-weight models running on your own machine via Ollama. No per-use cost at all, but limited by your hardware.

## Presets — pick your price/quality trade-off

Rather than choosing a model for every agent, you pick a **preset**:

- **`frugal`** — open-weight models for everything. The cheapest way to run. Best for exploring and for long backtests.
- **`hybrid`** — cheap models for the mechanical agents, quality models (Claude) for the decision-makers. The recommended day-to-day balance: most of the cost saving, most of the quality.
- **`research`** — quality models for the decision-makers, fast models for the analytical agents. A solid default.
- **`quality`** — the most capable models (including Opus) on the decision path. The most expensive; use it to validate a final configuration, not to explore.
- **`dev`** — open-weight/local models for everything; intended for development and offline experimentation.

## How to keep costs under control

- **Start cheap.** Explore on `frugal` or `hybrid`. Only move to `research` or `quality` once an idea has proven itself.
- **Always set a cost ceiling.** Every strategy has a **cost ceiling per cycle** — a hard cap. If a cycle's estimated cost would exceed it, the app trims the work to fit. Set one on every strategy, even a generous one, so a misconfiguration cannot quietly drain your budget.
- **Watch the live estimate.** The Run console and the backtest setup screen show a projected cost *before* you commit. Read it. If it surprises you, change the preset or shrink the job.
- **Size backtests deliberately.** Test a short window and a small universe first. Lengthen the window or widen the universe only after the cheap version looks promising.
- **Consider local models.** If you have capable hardware, running open-weight models through Ollama removes per-use cost entirely.

## The disclaimer, plainly stated

You are responsible for all charges incurred on your own AI and data provider accounts through your connected keys. Cost figures shown in the app are **estimates**: actual billing depends on your provider's current prices, the exact tokens used, and provider-side rounding. The app's cost ceilings limit work *the app schedules* — they cannot override or cap billing on your provider account itself. Set spending limits and alerts directly with your providers. Treat every backtest's projected cost as a number to check, not a number to trust blindly.
