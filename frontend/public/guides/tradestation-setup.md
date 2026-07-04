# TradeStation Setup

> [!NOTE] > **Not enabled yet.** Connecting a TradeStation account is not available in the
> app today — its tile in the connect wizard is marked _"ships in a later
> release."_ This guide documents the developer-app setup so you are ready when
> it lands. To connect a broker right now, see _Connect a Broker Account_
> (Alpaca paper, or the built-in Demo broker).

To connect a TradeStation account (paper or live) you need a TradeStation
developer application. Credentials are **bring-your-own** — the platform
never ships a shared TradeStation app, so each deployer registers their
own.

## Prerequisites

- A funded (or at least approved) **TradeStation brokerage account**.
  Sign up at `https://www.tradestation.com`. US residency is required;
  international accounts are limited.
- The same login also signs you in to the developer portal.

## Steps

### 1. Open a TradeStation brokerage account

Paper-only access is not enough — the developer portal is gated on a
real brokerage account.

### 2. Sign in to the Developer Portal

Go to `https://api.tradestation.com` and sign in with your TradeStation
credentials. Accept the API terms.

### 3. Create an Application

Portal → **My Apps** → **Create App**.

- **Name** — anything (e.g. _AIHedgeFund local_).
- **Redirect URI** — must match `TRADESTATION_REDIRECT_URI` exactly.
  - Local dev default: `http://localhost:8811/api/broker-accounts/oauth/callback/`
  - Production: `https://<your-host>/api/broker-accounts/oauth/callback/`
  - You can register multiple URIs (local + prod).
- **Scopes** — request `openid offline_access ReadAccount Trade`.
  The adapter deliberately does **not** request `MarketData` (quotes are
  re-priced via the system's FMP marks).
- **Grant type** — Authorization Code with PKCE.

### 4. Submit for approval

TradeStation manually reviews app submissions. Turnaround is typically
1–5 business days. They may email asking what the app does — _"personal
algorithmic trading assistant; SIM-only at this stage"_ is fine.

### 5. Copy your credentials

Once approved, the portal shows your **Client ID** and **Client Secret**.

### 6. Configure the backend

Export the env vars in your deployment (for local dev, add to your shell
profile or `infra/.env`):

```
TRADESTATION_CLIENT_ID=...
TRADESTATION_CLIENT_SECRET=...
TRADESTATION_REDIRECT_URI=http://localhost:8811/api/broker-accounts/oauth/callback/
```

Restart Django. The connect-wizard's _Step 1 — Developer app configured_
will flip green and _Step 2_ will hand back a real TradeStation
authorize URL.

## Caveats

- **Residency.** Israeli (and some other non-US) residents may not be
  able to open a TradeStation brokerage account at all — TradeStation's
  US-broker arm doesn't onboard everywhere. Check the country list
  before investing time in the application.
- **Paper = SIM.** The TradeStation "Simulated" environment is the
  paper venue. No separate sandbox app is needed — the same OAuth app
  works for both SIM and production; the adapter routes to the SIM host
  for paper accounts and the production host for live accounts.
- **Live execution is gated.** TradeStation in this codebase ships with
  `supports_live=False` — the live OAuth path is built and tested but
  live order entry is disabled until protective-bracket orders ship.
- **Refresh tokens.** TradeStation refresh tokens are long-lived but
  can be revoked or rotated. The wizard's _Reconnect TradeStation_
  banner handles re-auth in one click; no data is lost.

## Troubleshooting

- _Step 1 says "not configured":_ the backend doesn't see the env vars
  — restart Django after exporting them and confirm with
  `curl http://localhost:8811/api/broker-accounts/tradestation/runtime-config/`
  (expect `{"configured": true}`).
- _Authorize URL returns "invalid redirect_uri":_ the URI you registered
  on the developer portal does not exactly match
  `TRADESTATION_REDIRECT_URI`. Paths and trailing slashes count.
- _Callback "invalid or expired OAuth state":_ the user took longer
  than 10 minutes between clicking _Authorize_ and completing consent.
  Restart the connect flow.
- _"no refresh token on file — reconnect TradeStation":_ the
  `offline_access` scope was not granted (or was later revoked). Use
  the _Reconnect_ banner; the new handshake re-requests the scope.
