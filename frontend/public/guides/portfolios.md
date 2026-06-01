# Portfolios & Books

A **portfolio** — or **book**, the industry slang — is simply a set of holdings plus cash. The app keeps several kinds of book, all of them **paper** (simulated) today, and gives you one place to see them: the **Portfolios** page in the sidebar.

## The three kinds of book

- **Manual book** — your hand-managed paper portfolio. Everyone gets one automatically, seeded with **$100,000**. You add, edit, and close positions yourself, and you can push a decision into it straight from a run with *Add to portfolio*.
- **Strategy book** — each strategy you create owns its own book. It stays empty until you **enter** (enrol) one of the strategy's completed cycles into it. From then on it holds whatever that strategy decided.
- **Broker book** — each connected broker account has a book that mirrors what the broker reports. You don't edit it directly; it updates from your orders and fills (see *Connect a Broker Account* and *Placing Orders from a Run*).

## The Portfolios hub

The **Portfolios** page (*"Your books"*) lists every book you own. Tiles across the top total your **cash**, your **equity** (cash plus positions valued at cost), and the **number of books**. The table shows each book's **kind**, **cash**, **market value**, **equity**, and **position count**, and every row links to the surface that manages that book. Strategy books appear here only once they actually hold something.

## Inside a book

Open the **Manual book** to see the full anatomy of a portfolio — strategy and broker books read the same way.

Four headline numbers sit at the top:

- **Total value** — cash plus all positions valued at their latest **mark** (price). This is the book's **NAV** (Net Asset Value).
- **Unrealized P&L** — paper gain or loss on positions you still hold (mark value minus what you paid).
- **Net exposure** — long positions minus short, as a percent of NAV, with **gross** exposure (longs plus shorts) shown alongside. Net tells you which way the book leans; gross tells you how active it is.
- **Realized P&L** — profit or loss locked in on positions you have already closed.

Below that, the **Positions** table lists each holding with its quantity (negative means **short**), average cost, current mark, market value, unrealized P&L, and **weight** (its share of NAV). Underneath, the **Transaction ledger** is an append-only record of every cash movement and position event — deposits, opens, increases, closes, broker fills, and strategy enrolments — so the book's history is always auditable. Each position also remembers its **provenance**: whether it came from manual entry, a run, or a strategy cycle.

## Marks and how prices refresh

Positions are valued using **marks** — recent prices pulled from your market-data provider. A **cadence** control sets how fresh they are:

- **Daily** — the last daily close.
- **Delayed** — intraday quotes that refresh on a short interval.
- **Manual** — the same data, but only when you ask; the page never auto-polls.

A **Refresh marks** button pulls fresh prices on demand. Marks that have gone stale are flagged so you know a value may be out of date.

## Managing the Manual book

From the Manual book you can:

- **Add position** — open a holding by hand (ticker, quantity, average cost).
- **Cash deposit / withdraw** — adjust the book's cash.
- **Edit** or **Close** any position. Editing is correction-only; to change cash, use deposit/withdraw.

## Entering a strategy cycle into its book

When a strategy **cycle** finishes (status **done**), its page shows an **Enter strategy** button. This opens an enrolment preview listing the proposed moves — **open**, **increase**, **reduce**, or **close** — needed to make the book match the cycle's target. You can **Enter all** at once, or tick individual rows and **Enter selected**. Enrolment writes those positions into **that strategy's book only**; your Manual book is left untouched. Holdings in the book that aren't in the new target are closed. Afterwards the cycle is marked **enrolled**, and a link takes you to the book under Portfolios.

For the per-decision alternative — placing one position at a time from a run — see *Placing Orders from a Run*. For the metrics used to judge a strategy's book over time, see *Leaderboards*.
