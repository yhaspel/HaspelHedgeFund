# Always-on cloud deploy (Railway)

Run the whole stack — SPA, API, Celery worker, Celery beat, Postgres, Redis — 24/7
on [Railway](https://railway.com) so scheduled runs, autopilot cycles, order polls,
and notifications fire when your machine is closed.

This guide is written for a **single-user / personal instance**: one account (yours),
no multi-tenancy, no billing, no public signup. It works for a fresh install or for
**migrating an existing local instance** (dump + restore, keeping all history).

> **Why a cloud deploy at all?** Celery beat drives every scheduled run and autopilot
> cycle, and beat dies with your laptop. Nothing about the app requires the cloud —
> this is purely about being always-on.

**Cost:** ballpark **$15–25/month** for this footprint (≈2 GB RAM across six services,
modest CPU, a few GB of volume). Railway's Hobby plan includes $5 of usage. Verify
current rates at [railway.com/pricing](https://railway.com/pricing) and watch your
first month's usage graph. LLM spend is separate and unchanged in kind — but it now
runs around the clock, so set `RUN_DEFAULT_MAX_BUDGET_USD` (below).

---

## What you need first

- A Railway account (Hobby is enough).
- This repo on GitHub — your own fork or clone. Railway builds from the repo.
- If migrating: a working local stack and its `.env`, in particular
  **`FIELD_ENCRYPTION_KEY`** (see [Step 2](#step-2--bring-your-data-migrating-an-existing-instance)).
- Green CI on `main` (Railway can be told to deploy only after CI passes).

## Topology — one project, six services

| Service | Builder | Start command | Public? |
|---|---|---|---|
| **Web** | `infra/frontend.Dockerfile`, target `railway` | nginx (image default) | yes — the URL you use |
| **API** | `infra/backend.Dockerfile`, target `prod` | `migrate` → `collectstatic` → gunicorn on `$PORT` | yes (also `/admin/`) |
| **Worker** | same backend image | `celery -A hedgefund worker -l info --concurrency=2` | no |
| **Beat** | same backend image | `celery -A hedgefund beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler` | no |
| **Postgres** | Railway managed Postgres | — | private |
| **Redis** | Railway Redis template | — | private |

Two rules that matter:

- **Beat runs exactly one instance, ever.** Replicas = 1, never autoscaled. Two beats
  mean every schedule fires twice.
- **App-sleep / serverless stays off** on every service. The entire point is always-on.

---

## Step 1 — Project and Postgres

1. Create a new Railway project. **Pick your region at creation** (it can't be changed
   later) — choose the one closest to you; the app's own upstream calls (broker, market
   data, LLM) are background-async and latency-insensitive.
2. Add a **PostgreSQL** database to the project.
3. From the Postgres service's **Variables** tab, note `DATABASE_PUBLIC_URL` (the
   TCP-proxy endpoint you can reach from your machine) and the `PG*` variables.
4. Note the **Postgres major version** (the service's image tag, e.g. `postgres:17`).
   You need it in the next step.

## Step 2 — Bring your data (migrating an existing instance)

Skip this whole step for a fresh install; just let the API service run migrations on
first boot and create a superuser afterwards.

**Do the restore before the app services first boot.** Then `migrate --noinput` on the
API's first deploy is a harmless no-op against an already-migrated schema, instead of
racing a restore into a half-created one.

### 2a. Make your encrypted columns portable *first*

BYO provider keys and broker credentials are encrypted at rest. If your local `.env`
has **no** `FIELD_ENCRYPTION_KEY`, those columns are encrypted with a key derived from
`DJANGO_SECRET_KEY` — and your cloud instance will have a *different* secret key, so
they'd decrypt to nothing. Fix it before dumping:

```bash
# 1. Generate a real field-encryption key and add it to your local .env.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#    -> FIELD_ENCRYPTION_KEY=<that value>   (in .env)

# 2. RECREATE the containers — `docker compose restart` does NOT re-read env_file.
docker compose -f infra/docker-compose.yml up -d web worker beat

# 3. Confirm nothing is undecryptable (MultiFernet keeps the legacy key as secondary).
docker compose -f infra/docker-compose.yml exec web python manage.py vault_doctor
```

Then re-encrypt every stored ciphertext onto the new primary key, so the rows no longer
depend on the old `DJANGO_SECRET_KEY` at all:

```bash
docker compose -f infra/docker-compose.yml exec -T web python manage.py shell <<'PY'
from apps.brokers.models import BrokerCredential
from apps.models_catalog.models import ProviderKey
from apps.models_catalog.crypto import decrypt, encrypt

PK = ["anthropic_api_key_enc", "openrouter_api_key_enc", "openai_api_key_enc",
      "fmp_api_key_enc", "tiingo_api_key_enc", "fred_api_key_enc", "resend_api_key_enc"]
BC = ["encrypted_api_key", "encrypted_api_secret",
      "encrypted_access_token", "encrypted_refresh_token"]

def rotate(rows, fields):
    rotated = 0
    for row in rows:
        changed = []
        for f in fields:
            token = getattr(row, f, "") or ""
            if not token:
                continue
            plain = decrypt(token)
            if not plain:          # undecryptable — leave it; vault_doctor names it
                continue
            setattr(row, f, encrypt(plain))   # writes with the new primary
            changed.append(f)
        if changed:
            row.save(update_fields=changed)
            rotated += 1
    return rotated

print("ProviderKey rows rotated:", rotate(ProviderKey.objects.all(), PK))
print("BrokerCredential rows rotated:", rotate(BrokerCredential.objects.all(), BC))
PY
docker compose -f infra/docker-compose.yml exec web python manage.py vault_doctor  # -> 0
```

If some row resists rotation, proceed anyway: `decrypt()` degrades to `""` (the key
reads as "not set"), `vault_doctor` names the exact rows, and you re-enter those keys
once in **Settings → Providers** / reconnect the broker. Annoying, not dangerous.

### 2b. Quiesce, then dump

Take the snapshot with nothing mid-flight — no `Run`/`Backtest` in a running state and
no scheduled cycle due in the next ~30 minutes:

```bash
docker compose -f infra/docker-compose.yml stop web worker beat frontend   # db stays up
docker compose -f infra/docker-compose.yml exec -T db \
  pg_dump -U hedgefund -Fc hedgefund > backups/pre-cloud-$(date +%F).dump
shasum -a 256 backups/pre-cloud-*.dump
```

Also record a few row counts (`runs_run`, `backtests_backtest`, …) — you'll assert them
after the restore.

### 2c. Rehearse the restore locally (cheap, and it's the real proof)

This proves the *cloud* configuration decrypts before the cloud exists: restore into a
scratch Postgres, then boot the backend against it with **fresh** secrets and the
**same** `FIELD_ENCRYPTION_KEY`.

```bash
docker run -d --name pg-rehearsal -e POSTGRES_PASSWORD=x -p 5433:5432 postgres:16
docker run --rm --network host -v "$PWD/backups:/b" postgres:16 \
  pg_restore --no-owner --no-privileges -d "postgresql://postgres:x@127.0.0.1:5433/postgres" \
  /b/pre-cloud-<date>.dump
# vault_doctor against it, with prod-shaped throwaway secrets and the REAL field key:
#   DJANGO_ENV=prod DJANGO_SETTINGS_MODULE=hedgefund.settings.prod
#   DJANGO_SECRET_KEY=<throwaway> JWT_SIGNING_KEY=<throwaway>
#   FIELD_ENCRYPTION_KEY=<the same one from .env>
#   -> expect "0 undecryptable"
docker rm -f pg-rehearsal
```

### 2d. Restore into Railway's Postgres

The restore **client** must match the Railway Postgres **server** major version (a `-Fc`
dump from an older pg_dump restores fine into a newer server, as long as the client you
run matches that server):

```bash
docker run --rm -v "$PWD/backups:/b" postgres:<major> \
  pg_restore --no-owner --no-privileges -d "<DATABASE_PUBLIC_URL>" /b/pre-cloud-<date>.dump

# Spot-check against the counts you recorded:
docker run --rm postgres:<major> \
  psql "<DATABASE_PUBLIC_URL>" -c "select count(*) from runs_run;"
```

## Step 3 — Redis

Add the **Redis** template service to the project. Nothing to configure; it's referenced
by variable in the next step.

## Step 4 — API, Worker, Beat

Create **three** services from your GitHub repo (branch `main`), all using the
**Dockerfile** builder with path `infra/backend.Dockerfile` and **target `prod`**. Set
each one's start command:

- **API**
  ```sh
  sh -c "python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn hedgefund.wsgi:application --bind 0.0.0.0:${PORT:-8811} --workers 2 --timeout 120 --access-logfile - --error-logfile -"
  ```
  Generate a public domain; set the healthcheck path to `/api/health/`.
- **Worker** — `celery -A hedgefund worker -l info --concurrency=2` · no public networking.
- **Beat** — `celery -A hedgefund beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler` · no public networking, **replicas = 1**.

Two things to expect rather than debug:

1. A repo-connected service **starts building the moment it's created**, before its
   variables exist. That first deploy crash-loops harmlessly — set the variables, then
   redeploy.
2. Railway healthchecks arrive with **`Host: healthcheck.railway.app`**
   ([docs](https://docs.railway.com/guides/healthchecks)). That hostname is in the
   `DJANGO_ALLOWED_HOSTS` value below **by requirement, not decoration** — without it
   Django returns 400 to every probe and the deploy never goes healthy.

## Step 5 — Web

Create a **fourth** service from the same repo, Dockerfile `infra/frontend.Dockerfile`,
**target `railway`**. Add a service variable `API_ORIGIN=https://<your-api-domain>`
(Railway exposes service variables to Dockerfile builds as build args; the build fails
fast with a clear message if it's missing). Generate its public domain and set the
healthcheck path to `/health`.

`API_ORIGIN` must have **no trailing slash** — the Dockerfile strips one defensively,
because `proxy_pass https://host/;` (with a URI part) would rewrite away the `/api`
prefix and break every API call.

## Step 6 — Close the loop

1. Set `DJANGO_ALLOWED_HOSTS` and `FRONTEND_URL` now that both domains exist (below).
2. Turn on **Wait for CI** for all four repo-built services (Settings → GitHub
   autodeploys) so a push deploys only after CI passes.
3. Verify **Beat replicas = 1** and app-sleep **off** everywhere.

### Environment matrix

Set these on **API, Worker, and Beat** (Railway *shared variables* let you define them
once per project). The Web service needs only `API_ORIGIN`.

| Variable | Value | Notes |
|---|---|---|
| `DJANGO_ENV` | `prod` | activates the boot guard |
| `DJANGO_SETTINGS_MODULE` | `hedgefund.settings.prod` | |
| `DJANGO_SECRET_KEY` | fresh random, ≥50 chars | generate |
| `JWT_SIGNING_KEY` | fresh, ≥32 bytes — `python -c "import secrets; print(secrets.token_urlsafe(48))"` | invalidates existing sessions (you log in again) |
| `FIELD_ENCRYPTION_KEY` | **the exact key from Step 2a** | this is what makes migrated credentials decrypt |
| `DJANGO_ALLOWED_HOSTS` | `<api-domain>,<web-domain>,healthcheck.railway.app` | third entry **required** for healthchecks |
| `POSTGRES_DB` / `_USER` / `_PASSWORD` / `_HOST` / `_PORT` | `${{Postgres.PGDATABASE}}` / `${{Postgres.PGUSER}}` / `${{Postgres.PGPASSWORD}}` / `${{Postgres.PGHOST}}` / `${{Postgres.PGPORT}}` | Railway references — private network, no code change |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | reference |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | `${{Redis.REDIS_URL}}/0` / `${{Redis.REDIS_URL}}/1` | drop the suffix if the rendered URL already ends in a db number |
| `FRONTEND_URL` | `https://<web-domain>` | feeds CSRF/CORS in `prod.py` |
| `CORS_ALLOWED_ORIGINS` | `https://<web-domain>` | |
| `LLM_DEFAULT_PRESET` | `dev` | **pin it** — base settings default to `hybrid`, which routes decision agents to frontier models |
| `BLOCK_ANTHROPIC` | `1` | hard block on the Anthropic direct API |
| `ANTHROPIC_API_KEY` | **unset** | belt-and-suspenders with `BLOCK_ANTHROPIC` |
| `OPENROUTER_PAID_FALLBACK` | `1` | free-pool saturation falls back to small paid spend |
| `ALLOW_PLATFORM_DATA_KEYS` | `1` | on a personal instance the env keys *are* your keys |
| `RUN_DEFAULT_MAX_BUDGET_USD` | e.g. `5` | per-run backstop; a run crossing it aborts |
| `SIGNUP_ENABLED` | `0` | locks `POST /api/auth/signup/` to 403 |
| `FMP_API_KEY`, `OPENROUTER_API_KEY`, `FRED_API_KEY`, `TIINGO_API_KEY`, `EDGAR_USER_AGENT`, `RESEND_API_KEY` | copy from local `.env` | whichever you use |
| `EMAIL_BACKEND` | `apps.notifications.backends.ResendEmailBackend` | if you use Resend |
| `DEFAULT_FROM_EMAIL` | copy from local `.env` | |
| `SENTRY_DSN` | unset | optional; needs the `sentry` extra baked in |

Not needed: `ALPACA_*` bootstrap vars and `DJANGO_SUPERUSER_*` (accounts and credentials
arrive with the migrated database), `OFFLINE_MODE` / `OLLAMA_HOST` (defaults are right;
the health probe reports `local_available: false` harmlessly), IBKR/TradeStation vars.

---

## Smoke sequence

Run these in order — each isolates one layer:

1. `https://<api-domain>/api/health/` → `{"status":"ok", …}` (Django up, DB reachable).
2. `https://<web-domain>/health` → `ok` (nginx up).
3. `https://<web-domain>/api/health/` → the same JSON (the proxy path works).
4. `https://<web-domain>/` → the SPA loads; log in with your existing account.
5. History intact: NAV history renders; runs/backtests/strategies/leaderboard populated;
   autopilot state preserved. Spot-check row counts against your pre-dump numbers.
6. `vault_doctor` **in the cloud** → 0 undecryptable. Run it via `railway ssh`, or
   temporarily prepend it to the API start command. Plain `railway run` executes on
   *your* machine, where the private `PGHOST` is unreachable. Then confirm
   **Settings → Providers** shows keys as "set" and brokers connected without re-auth.
7. `POST /api/auth/signup/` → **403**.
8. Beat proof: watch the beat logs for a dispatch tick, then trigger one scheduled run
   end-to-end — it completes, the costs page shows the spend, notifications arrive.
9. Broker proof: order-poll / reconcile cycles run clean in the worker logs.

**Soak it.** Leave your machine off through the next scheduled cycle and verify the cycle
ran, orders were gated/submitted/reconciled, and no task backlog piled up.

## ⚠️ Don't run two beats — the double-trading guard

After cutover, your **local** stack must never trade against the same broker accounts
again: a second beat + worker double-submits every autopilot cycle.

- Keep the local stack **down** by default.
- Set `PAPER_AUTO_SUBMIT_ENABLED=0` in your local `.env` **now**. It only gates paper
  auto-submit, so manual local dev keeps working; re-enabling is a deliberate flag flip.
- Disable any local cron/launchd jobs that assumed the local stack was the live one.

## Steady state

- **Deploys:** push to `main` → CI → Railway deploys on green (Wait for CI). If it ever
  sticks, hit **Deploy** in the dashboard. Migrations run on API boot.
- **Backups:** the cloud DB is now the source of truth. Dump it on a schedule from your
  machine against `DATABASE_PUBLIC_URL` — see
  [`backup-restore.md`](./backup-restore.md#backing-up-a-cloud-hosted-database) — and
  enable Railway's native Postgres backups if your plan has them.
- **Costs:** watch the Railway usage graph the first month, and the app's
  **Settings → Costs** page weekly (LLM spend now accrues 24/7).
- **Logs:** `railway logs` per service; every line carries a `request_id` or `run_id`.
- **EDGAR filing cache:** `MEDIA_ROOT/filings/` is ephemeral here, so migrated filing
  rows serve empty sections until they re-cache, and every redeploy clears it again.
  Acceptable for a personal instance; if filings-based analysis suffers, attach a Railway
  volume to the service that ingests (volumes bind to a single service).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Deploy never goes healthy; 400s in logs | `healthcheck.railway.app` missing from `DJANGO_ALLOWED_HOSTS` | add it |
| SPA loads but every API call hits `localhost:8811` | built without the production `fileReplacements` | ensure you build the `railway`/`production` target from current `main` |
| API calls 404 through the Web service | `API_ORIGIN` had a trailing slash | remove it and rebuild |
| Env change seemingly ignored locally | `docker compose restart` doesn't re-read `env_file` | `docker compose up -d <svc>` to recreate |
| `pg_restore` version error | restore client ≠ server major | rerun with `postgres:<server-major>` |
| Every schedule fires twice | Beat scaled >1 | set replicas back to 1 |
| Provider keys read as "not set" after cutover | `FIELD_ENCRYPTION_KEY` mismatch | set the exact Step 2a key; else re-enter keys in Settings → Providers |
