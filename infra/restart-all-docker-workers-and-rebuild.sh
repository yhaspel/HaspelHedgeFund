#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Heaviest rebuild: rebuild every image from scratch, ignoring Docker's
# layer cache, then recreate the containers. Use when a cached layer is
# stale/corrupt and restart-rebuild.sh didn't pick up a change.
# Named volumes (pgdata) survive, so DB data is kept.
docker compose build --no-cache
docker compose up -d
docker compose ps
