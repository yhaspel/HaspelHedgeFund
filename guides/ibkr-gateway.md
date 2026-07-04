# Interactive Brokers — Client Portal Gateway setup

The IBKR adapter (Phase 3a-2) talks to IBKR's **Client Portal Web API** through a small **Client Portal Gateway** — a headless Java service that IBKR distributes and that holds the brokerage session. The gateway runs as a Docker sidecar alongside the rest of the app.

You only need this guide if you want to connect an **Interactive Brokers** account. Demo, TradeStation, and Alpaca users can ignore it.

> [!NOTE]
> The gateway is **the only thing** that talks directly to IBKR. The Django backend talks to the gateway over the compose network; the gateway talks to IBKR. We never store your IBKR username, password, or 2FA — the gateway holds the session, in memory, inside the container.

## How it works at a glance

```
You              your browser              Docker
                                            ┌──────────────────────────┐
                                            │ ibkr-gateway (this guide)│
log in once  ─►  https://localhost:5000 ─►  │ - listens on 0.0.0.0:5000│
                                            │ - holds the IB session   │
                                            │ - talks to IBKR servers  │
                                            └──────────────────────────┘
                                                       ▲
                                                       │  compose network
                                                       │  https://ibkr-gateway:5000
                                            ┌──────────┴────────────────┐
                                            │ Django backend            │
                                            │ - IBKRBroker adapter      │
                                            │ - keep_gateway_warm task  │
                                            └───────────────────────────┘
```

Two characteristics of IBKR's API drive everything else in this guide:

1. **Logging in requires you, interactively, once a day.** IB mandates 2FA on every session start; there is no headless username/password path for retail accounts. You log in to the gateway via your browser; it then holds the session.
2. **The session resets nightly.** IB cuts every gateway session at roughly midnight ET. The app's `keep_gateway_warm` task notices and flips the account to *Needs reauth*; you re-log via the same browser flow.

Design rationale for the gateway bootstrap and the nightly reply-loop reauth gate is recorded in the project's architecture notes.

## 1. Obtain the gateway distribution

IBKR ships the gateway only as a `.zip` — there is no official Docker image, and the download URL changes often enough that we don't pin one in this repo. You'll download it once, by hand, and drop it into the build context.

1. Sign in to **Client Portal** at `https://www.interactivebrokers.com/`.
2. Navigate to **User Settings → API → Settings**, and find the **Client Portal API** section.
3. Download the **Client Portal Gateway** `.zip`. The filename is typically `clientportal.gw.zip` (or a versioned variant such as `clientportal.beta.gw.zip`).
4. Read and accept IBKR's terms — this is your acceptance, not Docker's.
5. Save the file to:

       infra/ibkr-gateway/dist/clientportal.gw.zip

The `dist/` directory is gitignored. The file must be named exactly `clientportal.gw.zip` (the Dockerfile expects that filename).

If you ever need to upgrade the gateway, repeat steps 1–5 with the newer `.zip` and rebuild the image (next section). Pin a known-good version by recording the filename + download date somewhere outside this repo — IBKR doesn't publish an official changelog or SHA256.

## 2. Build the gateway image

From the project root:

```
docker compose -f infra/docker-compose.yml build ibkr-gateway
```

If the `.zip` is missing, you'll see:

```
ERROR: failed to solve: ... COPY failed: file not found
```

Re-read step 1.

## 3. Start the gateway

```
docker compose -f infra/docker-compose.yml up -d ibkr-gateway
```

To confirm it's up:

```
docker compose -f infra/docker-compose.yml ps ibkr-gateway
docker compose -f infra/docker-compose.yml logs --tail=50 ibkr-gateway
```

The gateway listens on:
- `https://localhost:5000` — only your browser, on this host, can reach it (we deliberately bind to `127.0.0.1` so the gateway isn't on a public NIC).
- `https://ibkr-gateway:5000` — only services on the compose network (the Django app, Celery workers).

## 4. Log in for the day

1. Open `https://localhost:5000` in your browser.
2. Accept the self-signed certificate warning. The cert is bundled with IBKR's distribution; the warning is expected.
3. Enter your IBKR username and password.
4. Complete 2FA — IBKR Mobile (push notification) or SMS, whichever you have configured.
5. You'll see "Client login succeeded." You're authenticated.

The session stays alive until midnight ET *and* until you've been idle for about six minutes. The `keep_gateway_warm` Celery task tickles the session every two minutes, so as long as Celery is running you don't need to do anything to keep it alive within the day.

When the daily reset fires, the **Accounts** page in the app will surface a *"Re-authenticate your IBKR gateway"* banner. Click it (or just re-open `https://localhost:5000`), repeat steps 1–4, and the banner clears within ~2 minutes of the next `keep_gateway_warm` tick.

## 5. Connect an account in the app

In the app:

1. Go to **Broker accounts → Connect an account**.
2. Pick **Interactive Brokers**, mode **Paper**.
3. The wizard:
   - probes the gateway (Step 1 — should pass since you just logged in),
   - hands off to the gateway login (Step 2 — only fires if you aren't already logged in),
   - lists the IBKR accounts the session can see (Step 3),
   - asks you to pick one and confirms it's a paper account (Step 4 — paper accounts start with `DU`).
4. Done. The account shows up on the Accounts page with `connection_status="active"`.

## Stopping the gateway

```
docker compose -f infra/docker-compose.yml down ibkr-gateway
```

Stopping the gateway means every IBKR account flips to *Needs reauth* on the next `keep_gateway_warm` tick — that's by design. You'll restart and re-login the next time you want to trade.

## Split-host deployment

If you want to run the gateway on a *different machine* from the Django app (e.g. a desktop with a static IP for the daily login, and a server for the rest of the stack), set both URLs explicitly:

```
# in your .env or infra/docker-compose.override.yml
IBKR_GATEWAY_BASE_URL=https://gateway.lan:5000
IBKR_GATEWAY_LOGIN_URL=https://gateway.lan:5000
```

The Django backend uses `IBKR_GATEWAY_BASE_URL` for its server-side calls; the connect wizard surfaces `IBKR_GATEWAY_LOGIN_URL` to your browser as a clickable link.

The host that runs the gateway must publish port 5000 in a way *your browser can reach* — usually that means binding to the LAN interface, not just `127.0.0.1`.

## Troubleshooting

### "Gateway is unreachable" in the connect wizard

The gateway service isn't running. Check:

```
docker compose -f infra/docker-compose.yml ps ibkr-gateway
docker compose -f infra/docker-compose.yml logs --tail=100 ibkr-gateway
```

Then `docker compose -f infra/docker-compose.yml up -d ibkr-gateway`.

### "Could not find a DU-prefixed account" / mode mismatch

You logged into a live IBKR account but the wizard is set to *Paper* mode. Cancel the wizard and start again with the right mode — or log out of the gateway and back in with your paper credentials.

### "Already have IBKR account `DU…` connected"

You've already connected this account on another `BrokerAccount` row. Either use that one or disconnect it first from **Broker accounts → (the row) → Disconnect**.

### Daily reset rolled while I had an order in flight

3a-1's idempotency machinery already handles this: the in-flight order moves to `idempotency_state="unknown"` and the next reconcile pass adopts it via `find_order_by_client_id` once you re-authenticate. You won't get duplicate orders. See ADR 0008.

### The gateway login page shows the wrong language / region

IBKR's gateway respects browser locale. Set your browser language explicitly if it's matching the wrong region.

## Costs and licensing

The Client Portal Gateway is free to use with any IBKR account, but its licensing terms are IBKR's. By downloading it (step 1) you accept IBKR's terms. The project does not redistribute the gateway and does not pin a download URL — every upgrade is a deliberate, manual user action.
