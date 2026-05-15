# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:22-alpine AS dev
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules /app/node_modules
COPY frontend/ /app/
EXPOSE 4200
CMD ["pnpm", "ng", "serve", "--host", "0.0.0.0", "--port", "4200"]

FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules /app/node_modules
COPY frontend/ /app/
RUN pnpm build

FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
EXPOSE 80
