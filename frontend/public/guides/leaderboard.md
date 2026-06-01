# Leaderboards

The **Leaderboard** (in the sidebar, headed *Performance*) answers a blunt question: *which agents and strategies have actually produced good calls?* It scores them on real outcomes over a rolling **forward-test window**, so you can tell skill from luck and decide where to spend your model budget. Pick a window — **30 days**, **90 days**, or **Lifetime** — at the top, then switch between the **Agents** and **Strategies** tabs.

> [!NOTE]
> The leaderboard is computed from real forward returns, on a schedule, with **no extra AI cost** — it grades calls the council already made. Figures are daily snapshots, so they only get meaningful once you have run enough analysis to score.

## Agents tab

**Top personas** ranks the famous-investor agents by **hit rate** — the share of their directional (bullish/bearish) calls that turned out right. Alongside it you'll see:

- **Brier score** — how well-*calibrated* the agent's confidence is (lower is better). A persona that says "90% confident" should be right about 90% of the time.
- **n** — how many decisions the score is based on. More is more reliable.
- **Avg forward return** and **PnL** — the average move after the call, and its contribution to profit, both in **basis points** (bps; one bp = 0.01%).

Click any persona to drill into its **decision log** — every call it made, with the run, ticker, date, signal, confidence, and the actual 5-day forward return — and click through to the underlying run.

**Top models per role** ranks the AI **models** (not personas) by **cost-adjusted return** — forward return earned per dollar of model spend — grouped by the job the model was doing (persona, analytical, PM, risk, CIO, and so on). This is where you see whether an expensive model is actually paying for itself in a given role.

**Useful contrarians** ranks personas by how often they were right *when they went against the run's majority*. A persona that adds value mainly by disagreeing shows up here.

Anything scored on too few decisions is flagged **provisional** ("prov.") and should be read with caution.

## Strategies tab

**My strategies** lists your own strategies with the standard performance metrics — **Sharpe**, **Sortino**, **maximum drawdown**, **turnover**, and cost per cycle — plus one metric unique to this app:

- **Council alpha** — how much value the AI council added *beyond* a simple mechanical baseline of the same strategy. If it sits near zero, the council isn't earning its cost. The row also shows the council's **net value** (dollars it added or destroyed) and its **cost**. Council alpha needs roughly **30 days of paired baseline cycles** before it means anything, and stays blank until then.

Sort the table by **Sharpe**, **Council α**, or **Return**, and click a strategy to see its **Council alpha over time** chart — the realised, council-driven curve against the council-free baseline.

**Flavor benchmarks** aggregates across strategy *types* (long-only, market-neutral, pairs, and so on), showing the **median** Sharpe, Sortino, and drawdown for each, with the interquartile range (p25–p75) so you can see the spread. It's a quick way to judge whether one of your strategies is strong *for its kind*.

## How to use it

Use the Agents tab to prune your council — keep the personas and models that earn their keep, and reconsider the ones that are confidently wrong or simply expensive. Use the Strategies tab, and especially **Council alpha**, to decide whether the AI layer is worth it for a given strategy, or whether the mechanical baseline would do just as well for less. Definitions for every metric here are in the *Financial Terms & Abbreviations* guide.
