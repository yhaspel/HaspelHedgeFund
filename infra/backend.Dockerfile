# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Install deps from pyproject.toml. uv.lock is intentionally NOT copied: it is
# .gitignored (absent in a fresh CI checkout) and the install below reads only
# pyproject.toml, so COPYing it just made the image build fail in CI. (To make
# builds reproducible against the lock instead, commit uv.lock and switch this to
# `uv sync --frozen`.)
COPY backend/pyproject.toml /app/
RUN uv pip install --system --requirement pyproject.toml

COPY backend/ /app/

FROM base AS dev
CMD ["python", "manage.py", "runserver", "0.0.0.0:8811"]

FROM base AS prod
# P12 WS-1.3: no build-time collectstatic — at build time the DJANGO_* env is
# absent, so it silently no-opped. It moves to the API service's start command
# (runtime, where settings and the DB are real). $PORT is injected by the
# platform (Railway); the default keeps compose self-hosters on 8811.
CMD ["sh", "-c", "gunicorn hedgefund.wsgi:application --bind 0.0.0.0:${PORT:-8811} --workers 2 --timeout 120 --access-logfile - --error-logfile -"]
