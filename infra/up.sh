#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Bring up the entire dev stack (db, redis, web, worker, beat, frontend) with
# Docker Compose: builds images (Docker's layer cache keeps re-runs fast), waits
# for the web container's startup migrations to finish, creates the dev
# superuser if DJANGO_SUPERUSER_* are set in ../.env, then prints the URLs.
# This is the fresh-clone / first-run entry point; restart-rebuild.sh is for a
# plain dependency rebuild and restart-and-full-db-refresh.sh wipes the DB.
#
# Idempotent + non-destructive: the named pgdata volume is preserved, so this is
# safe to re-run on an existing stack. For a clean database, use
# restart-and-full-db-refresh.sh instead.
#
# The IBKR gateway sidecar stays off here (P3a-2 is deferred, so it sits behind
# the `ibkr` compose profile). Opt in once you have the BYO clientportal.gw.zip:
#     docker compose --profile ibkr up
# (see guides/ibkr-gateway.md).

# Auto-renew the frontend's anonymous node_modules volume when its pnpm lockfile
# drifts from the image the volume was seeded with. Shared with restart-rebuild.sh.
source ./frontend-deps-guard.sh

# web/worker/beat load ../.env via `env_file`, so compose hard-fails without it.
# On a fresh clone, seed it from the committed example; dev defaults boot as-is.
if [ ! -f ../.env ]; then
  echo ">> No ../.env found -- seeding it from ../.env.example..."
  cp ../.env.example ../.env
  echo "   Edit ../.env to add API keys; the dev defaults work out of the box."
fi

echo ">> Building images and starting the full stack..."
docker compose up -d --build

# A reused anon node_modules volume can mask the freshly-built image's deps; if
# the lockfile changed since the volume was seeded, renew just that volume.
renew_frontend_node_modules_if_lockfile_changed

echo ">> Waiting for the web container's startup migrations to finish..."
ready=""
for _ in $(seq 1 60); do
  if docker compose exec -T web python manage.py migrate --check >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ -z "$ready" ]; then
  echo "!! web did not become ready within 120s. Recent logs:" >&2
  docker compose logs --tail=50 web >&2
  exit 1
fi

# createsuperuser --noinput needs DJANGO_SUPERUSER_EMAIL + _PASSWORD; those ship
# blank in .env.example, so only attempt it when ../.env actually fills them in
# -- otherwise tell the user how to make one (matching the README). An existing
# superuser is left untouched: createsuperuser no-ops, it never resets a password.
if grep -qE '^DJANGO_SUPERUSER_EMAIL=.+' ../.env \
   && grep -qE '^DJANGO_SUPERUSER_PASSWORD=.+' ../.env; then
  echo ">> Ensuring the dev superuser exists (from DJANGO_SUPERUSER_* in ../.env)..."
  docker compose exec -T web python manage.py createsuperuser --noinput \
    || echo "   (skipped: a superuser with that email already exists)"
else
  echo ">> No DJANGO_SUPERUSER_* set in ../.env -- create an admin login with:"
  echo "   docker compose -f infra/docker-compose.yml exec web python manage.py createsuperuser"
fi

docker compose ps

cat <<'EOF'

Stack is up:
  Angular UI    -> http://localhost:4111/
  Django admin  -> http://localhost:8811/admin/
  API health    -> http://localhost:8811/api/health/

Follow logs with:  docker compose -f infra/docker-compose.yml logs -f
Stop the stack:    docker compose -f infra/docker-compose.yml down
EOF
