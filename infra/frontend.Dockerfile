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

# P12 WS-1.5: Railway/PaaS variant — same SPA config plus /health and an /api/
# proxy to the API service. The config is baked at build time because Railway
# ignores the nginx image's runtime entrypoint templating; API_ORIGIN arrives as
# a build arg (Railway exposes service variables to Dockerfile builds).
# The compose `prod` target above stays untouched for self-hosters.
FROM nginx:1.27-alpine AS railway
ARG API_ORIGIN
RUN test -n "$API_ORIGIN" || (echo "API_ORIGIN build arg required" && false)
COPY infra/nginx.railway.conf.template /tmp/nginx.template
# Normalize: strip any trailing slash — `proxy_pass https://host/;` (with a URI
# part) would rewrite away the /api prefix and break every API call.
#
# The `nginx -t` syntax check runs against a COPY of the rendered config whose
# upstream is swapped for a literal IP. `nginx -t` resolves proxy_pass hostnames
# at parse time, so testing the real config would make this image build depend on
# the API domain's DNS — it fails outright for a not-yet-created service or a
# reserved placeholder domain. The copy still proves the template + envsubst
# output is valid nginx; the real hostname is resolved by nginx at runtime.
RUN API_ORIGIN="${API_ORIGIN%/}" \
    && API_HOST=$(echo "$API_ORIGIN" | sed 's|https\?://||') \
    && export API_ORIGIN API_HOST \
    && envsubst '$API_ORIGIN $API_HOST' < /tmp/nginx.template > /etc/nginx/conf.d/default.conf \
    && cp /etc/nginx/conf.d/default.conf /tmp/real.conf \
    && sed 's|proxy_pass [^;]*;|proxy_pass http://127.0.0.1:8811;|' /tmp/real.conf \
       > /etc/nginx/conf.d/default.conf \
    && nginx -t \
    && cp /tmp/real.conf /etc/nginx/conf.d/default.conf \
    && rm -f /tmp/real.conf \
    && grep -q "proxy_pass ${API_ORIGIN};" /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
EXPOSE 80
