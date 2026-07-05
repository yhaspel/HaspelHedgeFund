# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS base
WORKDIR /app
RUN npm install -g pnpm@11.1.2

FROM base AS deps
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts
# Stamp node_modules with the lockfile's hash so the dev stack can detect when a
# reused anonymous /app/node_modules volume was seeded from a stale image (see
# infra/frontend-deps-guard.sh). Docker seeds an anon volume only once, so a
# lockfile bump otherwise leaves the old volume masking the new image's deps.
RUN sha256sum pnpm-lock.yaml | cut -d' ' -f1 > node_modules/.deps-lock-hash

FROM base AS dev
COPY --from=deps /app/node_modules /app/node_modules
COPY frontend/ /app/
EXPOSE 4111
# Invoke the local ng binary directly; going through `pnpm` triggers pnpm's
# depsStatusCheck which hard-fails on ignored postinstall build scripts
# (esbuild, @parcel/watcher, lmdb, msgpackr-extract) under pnpm 11+.
CMD ["node_modules/.bin/ng", "serve", "--host", "0.0.0.0", "--port", "4111"]

FROM base AS build
COPY --from=deps /app/node_modules /app/node_modules
COPY frontend/ /app/
RUN pnpm build

FROM nginx:1.27-alpine AS prod
# P4-OFF WS-2.7: SPA-aware config so deep-link refreshes serve index.html (not
# 404) and index.html / ngsw.json stay uncached for clean deploys.
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
EXPOSE 80
