from .base import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULER = "celery.beat:PersistentScheduler"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Provider clients refuse to init without keys; tests replay HTTP via VCR
# cassettes so the values are never sent to the wire.
FMP_API_KEY = "test-fmp-key"
OPENROUTER_API_KEY = "test-openrouter-key"
ANTHROPIC_API_KEY = "test-anthropic-key"
EDGAR_USER_AGENT = "AIHedgeFund Tests tests@example.com"
FRED_API_KEY = "test-fred-key"
TIINGO_API_KEY = "test-tiingo-key"
