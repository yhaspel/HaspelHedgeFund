#!/usr/bin/env bash
# infra/frontend-deps-guard.sh -- sourced by up.sh and restart-rebuild.sh.
# Not meant to be run directly; it only defines a function and inherits the
# caller's `set -euo pipefail` and infra/ working directory.
#
# Why this exists
# ---------------
# The `frontend` service mounts an ANONYMOUS volume over /app/node_modules
# (docker-compose.yml) so the container uses the image's Linux-built
# node_modules instead of the host's macOS one. Docker seeds an anonymous
# volume only the FIRST time it is created; every later `up --build` reuses the
# existing volume and ignores the freshly-built image's node_modules. So a
# frontend dependency bump (package.json / pnpm-lock.yaml) lands in the new
# image, but the stale volume keeps masking it -- `ng serve` then fails to
# compile ("Cannot find module ...") while the port still maps, so the app
# looks mysteriously down.
#
# How the guard works
# -------------------
# frontend.Dockerfile bakes sha256(pnpm-lock.yaml) into
# /app/node_modules/.deps-lock-hash at build time, so any volume seeded from an
# image carries the hash of the lockfile that image was built from. This guard
# reads that stamp from the running container's volume and compares it against
# the current host lockfile; on a mismatch it renews ONLY the frontend's
# anonymous volume (nothing else in the stack has one) so the new deps reach the
# container. When they already match it is a no-op.

# Hash the host lockfile. Callers cd into infra/ first, so it is at ../frontend/.
# Prefer sha256sum (Linux / CI); fall back to shasum (macOS ships no sha256sum).
_frontend_lockfile_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum ../frontend/pnpm-lock.yaml | cut -d' ' -f1
  else
    shasum -a 256 ../frontend/pnpm-lock.yaml | cut -d' ' -f1
  fi
}

renew_frontend_node_modules_if_lockfile_changed() {
  local want have
  want="$(_frontend_lockfile_hash 2>/dev/null || true)"
  if [ -z "$want" ]; then
    echo ">> (frontend-deps-guard: could not hash ../frontend/pnpm-lock.yaml; skipping)" >&2
    return 0
  fi

  # The stamp the running container's node_modules volume was seeded with.
  # Empty on a pre-guard (unstamped) volume, or if the container is not up --
  # either way that counts as a mismatch, which correctly forces a renew.
  have="$(docker compose exec -T frontend cat /app/node_modules/.deps-lock-hash 2>/dev/null | tr -d '[:space:]' || true)"

  if [ "$want" = "$have" ]; then
    return 0
  fi

  echo ">> Frontend lockfile changed since the node_modules volume was seeded;"
  echo "   renewing that volume so the rebuilt image's deps reach the container..."
  docker compose up -d --force-recreate --renew-anon-volumes frontend
}
