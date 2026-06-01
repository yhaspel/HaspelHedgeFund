# What This Application Is

The AI Hedge Fund is a tool for **researching investment strategies with the help of AI**. It runs a team of AI "agents" — each one modelled on a famous investor or a specific kind of financial analysis — that study stocks, debate them, and produce buy/sell decisions. You can watch that decision happen, build automated strategies around it, and test those strategies against historical market data.

It is built for **learning and experimentation**. Think of it as a flight simulator for investing: a realistic environment where you can try ideas and see what would have happened, without any real money at risk.

## What you can do with it

There are three core activities. Each has its own guide.

- **Runs** — a one-off analysis. You pick some stocks, the AI council studies them, and you get a recommendation with the full reasoning. This is the quickest way to see the app work.
- **Strategies** — a standing portfolio that makes its own decisions. You choose a *strategy type* (for example "Long/Short" or "Pairs Trading"), set its rules, and run it in **cycles** that decide what to hold.
- **Backtests** — a replay of a strategy against the past. The app steps day by day through historical data and shows how the strategy would have performed, so you can judge an idea before trusting it.

Around those three, you can connect a **paper broker** and place orders from a run, track every holding on the **Portfolios** page, automate analysis on a **schedule**, and see what's actually working on the **Leaderboard**. Each has its own guide in the *Trading & Automation* section.

## What it is NOT

- **It is not real-money trading.** The app is *paper-trading* — every position and profit is simulated. You *can* connect a brokerage account, but only **paper** (simulated) accounts are supported today (Alpaca paper, plus a built-in Demo broker); live, real-money trading is disabled.
- **It is not financial advice.** The AI agents produce opinions, not recommendations you should act on with real money. They can be confidently wrong.
- **It is not free to run.** The AI models cost money per use. Please read the **Models & Cost Disclaimer** guide before running anything.
- **It is not a prediction machine.** A good backtest is a description of the past, not a promise about the future.

## Who it is for

Anyone curious about how systematic and AI-assisted investing works — whether you are a hobbyist investor, a student, or a developer exploring AI agents. **No finance background is assumed.** Every financial term the app uses is defined in plain language in the *Financial Terms & Abbreviations* guide.

## A good first session

1. Read the **Models & Cost Disclaimer** so there are no billing surprises.
2. Follow **Bring Your Own Key (BYOK) Setup** to connect your accounts.
3. Skim the **Financial Terms & Abbreviations** glossary — or just keep it open in another tab.
4. Do a small **Run** on two or three well-known stocks (see the **How To** guide).
5. When the Run makes sense, read a strategy guide and build your first **Strategy**.
