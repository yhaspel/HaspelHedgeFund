#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose restart
docker compose ps
echo ">> Celery beat is not restarted by this script; if you run it: docker compose -f infra/docker-compose.yml restart beat"
