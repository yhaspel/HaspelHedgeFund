# How To: Runs, Backtests & Strategies

This guide walks through the three things you can do in the app. If a term is unfamiliar, check the *Financial Terms & Abbreviations* guide.

## Before you start

The app needs two kinds of account keys to function: an **AI provider key** (so the agents can think) and **market-data keys** (so they have prices and financials to think about). If you have not set these up, open the **Bring Your Own Key (BYOK) Setup** guide first — nothing below will work without them.

## How to run an analysis (a "Run")

A Run is a one-off study of a handful of stocks. It is the fastest way to see the council in action.

1. From the sidebar, open **Runs** and choose **New analysis**.
2. **Enter the tickers** you want analysed — a ticker is the short market code for a stock, like `AAPL` for Apple. Start small; two or three names is plenty for a first try.
3. **Choose the personas** — the famous-investor agents you want on the panel. More personas means a richer debate but a higher cost.
4. **Pick a model setup.** This is where cost is decided. The "Model overrides" panel shows a live cost estimate; if you are just exploring, choose the cheapest preset. See the **Models & Cost Disclaimer** guide.
5. Press run. You are taken to the Run's page, where each agent's opinion appears as it finishes, followed by the Portfolio Manager's final call.

**Reading the result:** each agent reports a *signal* (bullish, neutral, or bearish), a *confidence* score from 0 to 100, and a written *thesis*. Disagreement is normal and is preserved as *dissent* — the app never hides the minority view.

## How to build and run a Strategy

A Strategy is a standing portfolio that makes its own decisions on a repeating cycle. A Run answers "what about these stocks today?"; a Strategy answers "manage this book for me, over and over."

1. From the sidebar, open **Strategies** and choose **New strategy**.
2. **Pick the strategy type** (its "kind"). This is the most important choice — it decides *how* the book behaves. Each type has its own guide in the Strategies section; read the one you are considering.
3. **Set the universe** — the list of stocks or ETFs the strategy is allowed to choose from.
4. **Set the limits** — how concentrated a single position may be, how much trading is allowed, and a **cost ceiling per cycle** that caps AI spend. Always set a cost ceiling.
5. Save it. The strategy then runs **cycles** — each cycle, the council reviews the universe, the Portfolio Manager proposes trades, and the book is updated. Depending on your settings, trades may apply automatically or wait for you to approve them.
6. Open the strategy's page any time to see its holdings, recent cycles, and the reasoning behind each trade.

## How to run a Backtest

A Backtest replays a strategy against historical data so you can judge it before committing.

1. From the sidebar, open **Backtests** and choose **New backtest**.
2. **Choose the strategy settings** to test and a **date range** to replay.
3. **Choose the model setup carefully.** A backtest re-runs the council for every step in the window, so cost scales with *window length × number of stocks × model price*. A long backtest on premium models can be the single most expensive thing you do in the app. The setup screen shows a projected cost — read it before you confirm.
4. Run it. The engine steps through history **point-in-time** — at each date it only sees information that existed then, so the test is not cheating by using hindsight.
5. **Read the results:** an equity curve (the portfolio's value over time), and summary metrics — total return, Sharpe ratio, maximum drawdown, turnover. All of these are defined in the glossary.

## Comparing backtests

From a backtest's page you can open a **Compare** view to put two runs side by side — for example the same strategy with a cheap model versus an expensive one, or with the AI council on versus off. This is how you find out whether a setting actually earns its cost.

## A sensible workflow

Run a small analysis to build intuition → pick a strategy type and read its guide → backtest it on a modest window with a cheap model → only then increase the window, the universe, or the model quality. Let cost rise *after* an idea has shown promise, never before.
