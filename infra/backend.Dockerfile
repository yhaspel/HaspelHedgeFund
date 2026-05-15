# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

COPY backend/pyproject.toml /app/pyproject.toml
RUN uv pip install --system \
    'django>=5,<6' 'djangorestframework>=3.15' 'djangorestframework-simplejwt>=5.3' \
    'django-cors-headers>=4.4' 'django-celery-beat>=2.7' 'psycopg[binary]>=3.2' \
    'celery>=5.4' 'redis>=5' 'python-json-logger>=2' 'gunicorn>=22'

COPY backend/ /app/

FROM base AS dev
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM base AS prod
RUN python manage.py collectstatic --noinput || true
CMD ["gunicorn", "hedgefund.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
