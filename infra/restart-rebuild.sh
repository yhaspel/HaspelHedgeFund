#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Rebuild images (using Docker's layer cache) and recreate only the
# containers whose image changed. Use when a dependency changed
# (uv.lock / package.json); plain restart.sh suffices for code edits
# since the backend/frontend source is volume-mounted.
source ./frontend-deps-guard.sh
docker compose up -d --build
# A frontend dependency bump rebuilds the image, but the anon node_modules
# volume is only seeded once -- renew it when the lockfile drifted so the new
# packages actually reach the container (otherwise `ng serve` fails to compile).
renew_frontend_node_modules_if_lockfile_changed
docker compose ps
