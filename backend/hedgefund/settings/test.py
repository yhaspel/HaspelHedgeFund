from .base import *  # noqa: F401,F403

# Test-only sentinels. Override the env vars the docker-compose stack sets
# (which are short, dev-only values) so the JWT-length and CORS-default
# assertions stay independent of the container env.
SECRET_KEY = "dev-insecure-not-for-prod-not-for-prod-32b-sentinel"
SIMPLE_JWT = {  # noqa: F405 - re-declares base.SIMPLE_JWT
    **SIMPLE_JWT,  # noqa: F405
    "SIGNING_KEY": SECRET_KEY,
}
CORS_ALLOWED_ORIGINS = ["http://localhost:4111", "http://127.0.0.1:4111"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULER = "celery.beat:PersistentScheduler"

# P3b: assertable email delivery — mail.outbox captures sent messages.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
# P3b: deterministic Telegram host for respx-mocked delivery tests.
TELEGRAM_API_BASE = "https://api.telegram.test"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Provider clients refuse to init without keys; tests replay HTTP via VCR
# cassettes so the values are never sent to the wire.
FMP_API_KEY = "test-fmp-key"
OPENROUTER_API_KEY = "test-openrouter-key"
ANTHROPIC_API_KEY = "test-anthropic-key"
EDGAR_USER_AGENT = "AIHedgeFund Tests tests@example.com"
FRED_API_KEY = "test-fred-key"
TIINGO_API_KEY = "test-tiingo-key"

# P2n: tests use the env-var fallback path by default so existing cassettes
# (which expect FmpProvider() to work without a per-user key) keep passing.
# Tests that exercise the prod-deny path flip this to False with override_settings.
ALLOW_PLATFORM_DATA_KEYS = True
