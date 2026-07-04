# HaspelHedgeFund

**An AI hedge fund for the everyday investor** — a self-hosted, bring-your-own-key
research and paper-trading platform. A council of LLM analyst agents debates each
ticker, walk-forward backtests strategies, screens the market, and can run an
autonomous **paper** fund on Alpaca. You supply your own API keys; nothing is
proxied through a hosted service.

> ⚠️ **Educational / research software — not investment advice, no warranty.**
> Trading is **paper-only by design**: live-broker auto-execution is blocked in
> code. Nothing here recommends any security. See [Disclaimer](#disclaimer).

## What it does

- **Agent council** — a panel of persona LLM agents (value, macro, technicals, …)
  debate and vote a decision per ticker, with their full reasoning surfaced.
- **Backtesting** — walk-forward, out-of-sample-scored backtests, with a §9
  validation gate a strategy must clear before its autopilot can go live (paper).
- **Screener** — rank the market on fundamentals / technicals / factors.
- **Strategies & autopilot** — compose strategies and let a paper-broker autopilot
  rebalance them.
- **BYOK** — every provider key is yours, stored encrypted at rest, never proxied.

**Stack:** Django 5 + DRF + Celery + Postgres 16 + Redis 7 (backend, managed with
`uv`); Angular 21 (frontend, managed with `pnpm`); Docker Compose for dev.

## Requirements

Docker + Docker Compose. The whole stack runs in containers — no local Python,
Node, or database needed for the recommended path.

## Quickstart

```bash
cp .env.example .env          # then add your keys — see "API keys" below
docker compose -f infra/docker-compose.yml up --build
```

Create your admin user (one-time, while the stack is running):

```bash
docker compose -f infra/docker-compose.yml exec web python manage.py createsuperuser
```

Then open:

- Angular UI → http://localhost:4111/
- Django admin → http://localhost:8811/admin/
- API health probe → http://localhost:8811/api/health/ (200, no auth — what compose / load balancers / smoke checks should use)

Database migrations run automatically when the `web` container boots.

Restart all services:

```bash
./infra/restart.sh
```

**After a frontend dependency change** (anything touching `frontend/package.json` /
`pnpm-lock.yaml`), re-seed the container's node_modules — the dev service keeps
`node_modules` in an anonymous volume that `--build` alone does not refresh:

```bash
docker compose -f infra/docker-compose.yml up --build --renew-anon-volumes
```

`--renew-anon-volumes` recreates only anonymous volumes (node_modules); the named
`pgdata` Postgres volume is preserved.

## API keys

The app is **bring-your-own-key**. The minimum to run an analysis is **FMP**
(market data + fundamentals) and **OpenRouter** (LLM access). Everything else is
optional.

| Key | Purpose | Required? | Where to get it |
| --- | --- | --- | --- |
| `FMP_API_KEY` | Market data, fundamentals, news | **Required** | [financialmodelingprep.com](https://financialmodelingprep.com) |
| `OPENROUTER_API_KEY` | LLM access for the agent council | **Required** | [openrouter.ai](https://openrouter.ai) |
| `FRED_API_KEY` | Macro series | Optional | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) |
| `TIINGO_API_KEY` | News | Optional | [tiingo.com](https://www.tiingo.com) |
| `ANTHROPIC_API_KEY` | Claude direct (instead of via OpenRouter) | Optional | [console.anthropic.com](https://console.anthropic.com) |
| Ollama host | Free local models | Optional | see `guides/local-model-setup-guide.md` |
| `EDGAR_USER_AGENT` | SEC EDGAR filings — the SEC requires a real contact | Recommended | set your own: `Your Name you@example.com` |

**Two ways to supply keys:**

1. **Instance-wide (quickest):** put `FMP_API_KEY` + `OPENROUTER_API_KEY` in `.env`.
   Dev settings (`ALLOW_PLATFORM_DATA_KEYS=1` in `settings/dev.py`) make them work
   for every account on the instance.
2. **Per-user vault:** sign in → **Settings → Providers** and paste keys there
   (stored encrypted). Overrides the env keys for that user.

## Golden path (first run)

1. `docker compose … up --build` → `createsuperuser` → sign in at http://localhost:4111/.
2. **Run an analysis** on a ticker — the agent council debates and returns a
   decision with reasoning.
3. *(optional)* Run a **backtest** to clear the §9 validation gate, then **enable a
   strategy / autopilot** and connect an Alpaca **paper** account.

The in-app **Guides** (sidebar → Guides) cover every feature in depth — BYOK,
the agent council, backtesting, connecting a broker, and each strategy.

## Deployment posture

Designed for **local / trusted-network self-hosting.** Signup is **open by design**
(`SignupView` is `AllowAny`) so you can create your own account — **do not expose
the instance to the public internet as-is.** Before any non-local deployment, set
`DJANGO_ENV=staging|prod` and real values for `DJANGO_SECRET_KEY`,
`JWT_SIGNING_KEY`, and `FIELD_ENCRYPTION_KEY` (the boot guard refuses to start
otherwise).

## Costs & responsibility

Your keys, your API spend. You are solely responsible for complying with each
provider's Terms of Service. This software is provided **as-is**.

## Run backend without Docker (optional)

Requires Python 3.12, `uv`, and a local Postgres + Redis.

```bash
cd backend
uv sync                                  # creates .venv and installs deps
source .venv/bin/activate                # or: uv run <cmd> to skip activation
export DJANGO_SETTINGS_MODULE=hedgefund.settings.dev
export POSTGRES_HOST=localhost
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 0.0.0.0:8811
```

Run tests:

```bash
cd backend && uv run pytest
```

## Run frontend without Docker (optional)

Requires Node 22 LTS and `pnpm`.

```bash
cd frontend
pnpm install
pnpm start            # ng serve at http://localhost:4111
pnpm test --watch=false
pnpm build
```

## Autonomous Fund (paper, educational)

Provision the 3-account autonomous **paper** fund (one strategy per account):

```bash
# Reads the ALPACA_PAPER_{1,2,3}_* triples + ALPACA_FUND_OWNER_EMAIL from .env.
uv run python manage.py bootstrap_autonomous_fund

# Demo/educational shortcut — also seed a passing validation backtest per
# strategy so each account's autopilot is enable-able out of the box:
uv run python manage.py bootstrap_autonomous_fund --broker mock --no-verify --seed-validation-backtest
```

Each strategy's autopilot stays **disabled** until it passes the §9 validation gate
(a `DONE` walk-forward backtest with positive out-of-sample Sharpe). On the
Autonomous Fund page, use **Set up → Run validation backtest** on a card, or seed
demo backtests for an existing fund:

```bash
uv run python manage.py seed_validation_backtests --user me@example.com
```

> The seed writes a clearly-labelled `[seed]` backtest (it does **not** run the LLM
> engine) and is opt-in — `DONE` backtests are protected history, so it is never
> auto-run during a normal bootstrap.

The IBKR gateway sidecar is gated behind a compose profile and skipped by default.
Once you have the BYO `clientportal.gw.zip` (see `guides/ibkr-gateway.md`), include
it with `docker compose -f infra/docker-compose.yml --profile ibkr up`.

## Disclaimer

HaspelHedgeFund is **educational and research software**, provided **as-is with no
warranty** (see [LICENSE](./LICENSE), MIT). It is **not investment advice** and does
not recommend any security. Trading is **paper-only by design**: live-broker
auto-execution is permanently blocked in code (a core invariant). Automated paper
submission is off unless you opt in per schedule (`auto_paper_submit`, default
off), and `PAPER_AUTO_SUBMIT_ENABLED=0` is a global kill switch that disables it
instance-wide. You are solely responsible for any use of this software and for
complying with your data/LLM providers' and broker's terms.

## License

[MIT](./LICENSE) © 2026 Yuval Haspel.

## Layout

```
backend/     Django project + apps
frontend/    Angular app (in-app Guides live under frontend/public/guides/)
guides/      BYO-integration setup guides (IBKR, TradeStation, local models)
infra/       Dockerfiles + compose
```
