#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Full DB refresh. DESTRUCTIVE: drops and recreates the Postgres database,
# so ALL DB data is permanently deleted. Frontend node_modules (anonymous
# volume) and Docker images are left untouched -- this only resets the DB.
#
# On the way back up the web container re-runs `migrate` (schema + the
# persona data migration) and `post_migrate` reseeds the model catalog;
# we then recreate the dev superuser non-interactively from
# DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD in ../.env.

echo ">> Ensuring db + redis are up, then stopping app services..."
docker compose up -d db redis
docker compose stop web worker beat

echo ">> Dropping and recreating the database (all data lost)..."
docker compose exec -T db sh -c \
  'dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'

echo ">> Flushing Redis (clears stale cache + queued Celery tasks)..."
docker compose exec -T redis redis-cli FLUSHALL >/dev/null

echo ">> Starting the full stack (web runs migrate + seeds on boot)..."
docker compose up -d

echo ">> Waiting for migrations to finish..."
for _ in $(seq 1 60); do
  if docker compose exec -T web python manage.py migrate --check >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ">> Recreating the dev superuser from DJANGO_SUPERUSER_* (../.env)..."
docker compose exec -T web python manage.py createsuperuser --noinput \
  || echo "   (skipped: superuser already exists, or DJANGO_SUPERUSER_* not set)"

docker compose ps
