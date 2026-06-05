# HaspelHedgeFund

AI-driven hedge fund research and (eventually) paper-trading platform.
Phase 0 ships only the foundations: Django + Angular + Postgres + Redis + Celery, dockerized, with auth.

## Stack

- **Backend:** Django 5, DRF, SimpleJWT, Celery, Postgres 16, Redis 7. Managed with `uv`.
- **Frontend:** Angular 21 (signals, standalone components), Tailwind. Managed with `pnpm`.
- **Infra:** Docker Compose for dev. CI on GitHub Actions.

## Run with Docker (recommended)

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up --build
```

Create a Django superuser (one-time, while the stack is running):

```bash
docker compose -f infra/docker-compose.yml exec web python manage.py createsuperuser
```

Then open:

- Angular UI → http://localhost:4111/
- Django admin → http://localhost:8811/admin/
- API health probe → http://localhost:8811/api/health/ (200, no auth required — what compose / load balancers / smoke checks should use)

Restart all services:

```bash
./infra/restart.sh
```

**After a frontend dependency change** (anything touching `frontend/package.json` / `pnpm-lock.yaml`), re-seed the container's node_modules — the dev service keeps `node_modules` in an anonymous volume that `--build` alone does not refresh, so `ng serve` would otherwise fail to resolve the new/changed package:

```bash
docker compose -f infra/docker-compose.yml up --build --renew-anon-volumes
```

`--renew-anon-volumes` recreates only anonymous volumes (node_modules) from the fresh image; the named `pgdata` Postgres volume is preserved.

The IBKR gateway sidecar is gated behind a compose profile (P3a-2 is deferred), so it is skipped by default. Once you have the BYO `clientportal.gw.zip` (see `guides/ibkr-gateway.md`), include it with `docker compose -f infra/docker-compose.yml --profile ibkr up`.

To trigger the demo Celery task, log into `/admin/` → Periodic Tasks → run `apps.accounts.tasks.ping` and watch the `worker-1` container logs.

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

## Autonomous Fund (paper, educational)

Provision the 3-account autonomous paper fund (see `development-plans/completed-dev-phases/phase-07-ultimate-hedgefund.md`):

```bash
# Reads the ALPACA_PAPER_{1,2,3}_* triples + ALPACA_FUND_OWNER_EMAIL from .env.
uv run python manage.py bootstrap_autonomous_fund

# Demo/educational shortcut — also seed a passing validation backtest per
# strategy so each account's autopilot is enable-able out of the box:
uv run python manage.py bootstrap_autonomous_fund --broker mock --no-verify --seed-validation-backtest
```

Each strategy's autopilot stays **disabled** until it passes the §9 validation gate
(a `DONE` walk-forward backtest with positive out-of-sample Sharpe). On the Autonomous
Fund page, use **Set up → Run validation backtest** on a card, or seed demo backtests
for an existing fund:

```bash
uv run python manage.py seed_validation_backtests --user me@example.com
```

> The seed writes a clearly-labelled `[seed]` backtest (it does **not** run the LLM
> engine) and is opt-in — `DONE` backtests are protected history, so it is never
> auto-run during a normal bootstrap.

## Run frontend without Docker (optional)

Requires Node 22 LTS and `pnpm`.

```bash
cd frontend
pnpm install
pnpm start            # ng serve at http://localhost:4111
pnpm test --watch=false
pnpm build
```

## Layout

```
backend/             Django project + apps
frontend/            Angular app
infra/               Dockerfiles + compose
development-plans/   Phase-by-phase roadmap (start at 00-master-plan.md)
```

See [`development-plans/README.md`](./development-plans/README.md) for the full plan.
