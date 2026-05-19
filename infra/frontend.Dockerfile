# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS base
WORKDIR /app
RUN npm install -g pnpm@11.1.2

FROM base AS deps
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts

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
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
EXPOSE 80
