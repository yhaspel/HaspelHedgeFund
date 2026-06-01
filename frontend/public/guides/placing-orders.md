# Placing Orders from a Run

When a run finishes, each decision in its **order ticket rail** — the right-hand column on a run's page — can be turned into a real order in one of two ways: added to a **paper book**, or **submitted as a broker order** to a connected account. This works the same whether the run is a one-off **analysis** or a **candidate run** inside a strategy cycle.

> [!NOTE]
> You need a completed run, and — for broker orders — a connected, active broker account (see *Connect a Broker Account*). Everything here is paper trading; no real money is involved.

## The order ticket rail

Open a run and look at the right-hand rail. Each decision shows the action (**BUY**, **SELL**, or **HOLD**), the ticker, the council's confidence, and a **target quantity** and **target weight**. The target quantity is illustrative — it is computed against a $100,000 reference portfolio, then replaced by your real account balance when you actually place the order.

Once the run is **done**, two buttons appear on each actionable decision:

- **Add to portfolio** — records the position in a paper **book** with no broker involved (see *Portfolios & Books*).
- **Submit as broker order** — sends the trade to a connected broker account.

A couple of decisions cannot be entered this way: **pair** decisions can't be placed manually in v1, and the buttons stay hidden until the run has finished.

## Submitting a broker order

Clicking **Submit as broker order** opens a two-step flow.

### Step 1 — the order ticket

Titled **"Submit as broker order"**, it asks for:

- **Broker account** — which connected account to use (shown as *label · broker · mode*).
- **Quantity mode** — **Whole shares** (default) or **Fractional**. Fractional is offered only if the broker supports it; otherwise it is greyed out.
- **Quantity (shares)** — pre-filled from the decision's target. In whole-share mode a fractional target is rounded *down*, and a note shows the remainder you are dropping (switch to Fractional to keep it).
- **Order type** — **Market** (fills at the prevailing price) or **Limit** (you set a **limit price**, the worst price you will accept).

Click **Review →** to create a **draft** order and move to confirmation.

### Step 2 — confirm and submit

Titled **"Confirm and submit order"**, this shows a one-line summary — for example *buy 100 AAPL · market · est. notional $12,345.00* — and applies safety gates before anything is transmitted:

- **Large orders.** For an estimated value over **$1,000** you must **type the ticker** to confirm. Because the app re-prices market orders on the server, it can ask for this even when the on-screen estimate looked smaller — just type the ticker and resubmit.
- **Live accounts.** A live account would also require typing a phrase containing **LIVE** plus an accepted trading disclaimer. This never triggers today, since only paper accounts exist.

Click **Confirm and submit** to transmit the order.

> [!WARNING]
> On the **Demo broker**, orders fill instantly at the current price. On **Alpaca paper**, the order goes to Alpaca's paper engine and fills according to market hours and conditions — it may sit as *working* until it fills.

## Order lifecycle

An order moves through **draft → confirmed → submitted →** then a final state of **filled**, **partial**, **cancelled**, **rejected**, or **error**. Watch it on the broker account's **overview** page:

- **Working orders** — submitted or partially filled orders, each with a **Cancel** button.
- **Recent fills** — completed executions, showing ticker, side, quantity, price, and time.

Drafts and confirmed-but-unsubmitted orders are gathered on the **Pending orders** page (under Broker accounts), where you can finish or discard them.

## Entering orders from a strategy run

A strategy's **cycle** produces one **candidate run** per name it considered. Open any candidate run from the strategy's *Cycles* section and you will find the **same order ticket rail** — so *Submit as broker order* and *Add to portfolio* behave exactly as above.

There is also a **bulk** path: when a cycle is **done**, the **Enter strategy** button materialises the cycle's *entire* target into that strategy's paper book in one step. That enrolment does not place broker orders; it is covered in *Portfolios & Books*.
