#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Rebuild images (using Docker's layer cache) and recreate only the
# containers whose image changed. Use when a dependency changed
# (uv.lock / package.json); plain restart.sh suffices for code edits
# since the backend/frontend source is volume-mounted.
docker compose up -d --build
docker compose ps
