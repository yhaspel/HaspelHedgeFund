# Schedules & Automation

A **schedule** runs a **watchlist** of tickers through the AI council automatically, on a repeating cadence, and tells you only when something **material** changes — so you can keep an eye on a list of names without launching a run by hand each day. Open **Schedules** from the sidebar (the page is headed *Automation*).

> [!WARNING]
> Scheduled runs spend real money on AI usage, on a recurring basis. Always set a **cost ceiling**, and prefer a cheap **model preset** for automation. See the *Models & Cost Disclaimer* guide.

## What gets scheduled

A schedule is tied to one of your **watchlists** (manage these on the Watchlist page). Each time it fires, it runs the council over every ticker in that watchlist. It does **not** notify you about every run — a **materiality gate** decides what is worth surfacing, so you hear about genuine changes (a signal flipping bullish to bearish, a large move in confidence, a first-time risk veto) rather than noise.

## Creating a schedule

Click **+ New schedule** and fill in the form:

1. **Name** — anything memorable, e.g. *"Daily quality check."*
2. **Watchlist** — which list of tickers to run.
3. **Frequency** — **Every weekday (Mon–Fri)**, **Every day**, **Specific days of the week**, or **Custom (advanced)** for a raw cron expression.
4. **Time of day** — in **US Eastern** time (shown for the non-custom options). A plain-language summary and the resolved cron string are previewed as you edit.
5. **Model preset** — the price/quality bundle to run with. Cheap presets are the sensible default for something that runs repeatedly.
6. **Cost ceiling (USD / run)** and **On breach** — cap the spend per fire, and choose what happens if the estimate exceeds it: **Degrade to a cheaper preset**, **Skip the run**, or **Run anyway, notify**.
7. **Notify via** — which notification channel receives the alerts (set channels up under Settings; see *Telegram Notifications Setup* for the Telegram option).
8. **Skip NYSE holidays / weekends (market-aware)** — on by default, so the schedule doesn't fire when the market is closed.

### Optional: auto-submit paper orders

A schedule can also turn each run's decisions into orders automatically. Tick **Auto-submit paper orders from each run's decision**, then pick a **paper account** and your limits:

- **Draft only** — create the orders but don't fill them, so you can review in the UI first.
- **Max orders per day** and **Max notional per day** — daily safety caps.

> [!NOTE]
> Auto-submit works with **paper accounts only** — live accounts are hard-blocked, by design. See *Connect a Broker Account* and *Placing Orders from a Run*.

Click **Create schedule** to save it.

## Managing schedules

Saved schedules appear under **Your schedules**, showing each one's watchlist, schedule (human-readable plus cron), preset, **next run**, and a status pill of **Active** or **Paused**. Per row you can:

- **Pause** / **Resume** — stop or restart firing without deleting anything.
- **Run now** — fire it immediately, regardless of the schedule.
- **History** — open the run log (see below).
- **Delete** — remove the schedule.

## History

The **History** view records every time a schedule fired: when it ran, its **status** (**pending**, **running**, **done**, **failed**, or **skipped**), how many runs it produced, how many notifications it sent, the estimated cost, and a short note (for example, that it was skipped on a market holiday or degraded to a cheaper preset to stay under the ceiling). It's the place to confirm a schedule is behaving and to see what it has been costing.
