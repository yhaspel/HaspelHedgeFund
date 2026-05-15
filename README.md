# HaspelHedgeFund

AI-driven hedge fund research and (eventually) paper-trading platform.
Phase 0 ships only the foundations: Django + Angular + Postgres + Redis + Celery, dockerized, with auth.

## Stack

- **Backend:** Django 5, DRF, SimpleJWT, Celery, Postgres 16, Redis 7. Managed with `uv`.
- **Frontend:** Angular 19+ (signals, standalone components), Tailwind. Managed with `pnpm`.
- **Infra:** Docker Compose for dev. CI on GitHub Actions.

## How to run (dev)

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml up --build
```

Then:

- Angular UI → http://localhost:4200
- Django API → http://localhost:8000/api
- Django admin → http://localhost:8000/admin

## Layout

```
backend/    Django project + apps
frontend/   Angular app
infra/      Dockerfiles + compose
development-plans/   Phase-by-phase roadmap (start at 00-master-plan.md)
```

See [`development-plans/README.md`](./development-plans/README.md) for the full plan.
