# Connect a Broker Account

By default everything in the app is **paper trading** — simulated money, no broker involved. Connecting a **broker account** lets the app turn a run's decisions into actual orders on that broker. Today the only real broker you can connect is **Alpaca**, and only its **paper** (simulated) environment — so you can practise the full order workflow end to end with no real money at risk. A built-in **Demo broker** is also available for trying the mechanics instantly.

> [!NOTE]
> **Paper only, for now.** Live, real-money trading is deliberately disabled. Alpaca connects in paper mode only; Interactive Brokers and TradeStation appear in the connect wizard but are marked *"ships in a later release"* and cannot be selected yet.

## Where broker accounts live

Open **Broker accounts** from the sidebar. The page lists every account you have connected, each with a status pill and a link to its **overview** — balances, working orders, and recent fills. A broker account is separate from your other books: each connected account gets its **own book** under *Portfolios* (see the *Portfolios & Books* guide).

## The two brokers you can connect today

### Demo broker

An in-memory simulator that needs no credentials and seeds **$100,000**. Orders fill immediately at the current price. It is the fastest way to see *Placing Orders from a Run* work, with nothing to set up — and nothing is real, so experiment freely.

### Alpaca (paper)

Alpaca is a commission-free US broker with a free **paper-trading** environment. The app connects only to that paper environment, using API keys you generate yourself in your own Alpaca account — the same bring-your-own-key approach the app uses for AI and market data (see *Bring Your Own Key (BYOK) Setup*).

> [!WARNING]
> The Alpaca adapter is currently marked **community-unverified** — it has not yet been fully tested end to end against a live Alpaca sandbox. Treat it as experimental, and check that orders behave as you expect.

## Connect Alpaca, step by step

1. **Get paper API keys.** Sign in at Alpaca and open the **paper-trading** dashboard (`app.alpaca.markets/paper/dashboard/overview`). Generate an **API key pair** — a **key ID** (begins `PK…`) and a **secret key**. The secret is shown only once, so copy it straight away. Make sure you are on the *paper* dashboard, not live; live keys are rejected.
2. **Start the wizard.** On **Broker accounts**, click **Connect**, then **Continue** on the **Alpaca (paper only)** tile.
3. **Name the account.** Give it a label such as *"My Alpaca paper"* and create it. The account starts in a **connecting** state.
4. **Paste your keys.** Enter the **API key ID** and **secret key**, then click **Connect**. The app validates them against Alpaca's paper API.
5. **Done.** On success the account flips to **active** and is ready to receive orders. If validation fails, re-check that the keys are *paper* keys and were pasted in full.

## Account statuses

Each account shows one of:

- **active** — credentials valid; ready to place orders.
- **connecting** — created but not yet validated; use **Resume setup** to finish.
- **needs reauth** — the session or token expired; reconnect to restore it.
- **disabled** / **error** — switched off by you, or an unrecoverable problem.

A **drift** pill can also appear when the app's record of the book has diverged from the broker's own record. Opening the account reconciles the two.

## Managing an account

From the **Broker accounts** list you can:

- **Open** an active account to see balances, working orders, and recent fills.
- **Disconnect** — clears the stored credential but keeps the account row and its history.
- **Delete** — removes the account entirely.
- Set the account's **default order quantity** to *whole shares* or *fractional*. Alpaca supports fractional shares; whole shares are the safe default. This is simply the starting point the order ticket uses — you can change it per order (see *Placing Orders from a Run*).

## What's next

With an active account, open any completed run and use **Submit as broker order** on a decision — covered step by step in *Placing Orders from a Run*. To see what a connected account is holding, read *Portfolios & Books*.
