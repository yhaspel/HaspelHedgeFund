#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Shut down the dev stack: stop and remove all containers + the compose network
# brought up by up.sh (db, redis, web, worker, beat, frontend, and the ibkr
# sidecar if it was started).
#
# Non-destructive: the named pgdata volume is preserved, so the database (and
# the frontend node_modules anon volume) survive -- `up.sh` brings everything
# back with the same data. To ALSO wipe the database, add `-v`:
#     docker compose down -v
# or use restart-and-full-db-refresh.sh for a clean DB without losing images.

echo ">> Stopping and removing the stack (pgdata volume preserved)..."
docker compose down --remove-orphans

echo ">> Stack is down. Bring it back up with: ./infra/up.sh"
