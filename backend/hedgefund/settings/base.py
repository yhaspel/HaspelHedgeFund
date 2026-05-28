import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Dev/test sentinels are intentionally insecure but 32+ bytes long so
# SimpleJWT does not emit "key shorter than 32 bytes" warnings on every test
# run. The guard at the bottom of this module still raises for non-dev envs.
_DEV_INSECURE_SENTINEL = "dev-insecure-not-for-prod-not-for-prod-32b-sentinel"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _DEV_INSECURE_SENTINEL)
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "django_celery_beat",
    "apps.accounts",
    "apps.data",
    "apps.runs",
    "apps.backtests",
    "apps.models_catalog",
    "apps.portfolios",
    "apps.screener",
    "apps.investor_profile",
    "apps.persona_evolution",
    "apps.brokers",
    "hedgefund_agents",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hedgefund.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "hedgefund.wsgi.application"
ASGI_APPLICATION = "hedgefund.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "hedgefund"),
        "USER": os.environ.get("POSTGRES_USER", "hedgefund"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "hedgefund"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("DJANGO_CONN_MAX_AGE", "300")),
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_USER_MODEL = "accounts.User"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

SIMPLE_JWT = {
    "SIGNING_KEY": os.environ.get("JWT_SIGNING_KEY", SECRET_KEY),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}
# SimpleJWT emits a UserWarning when SIGNING_KEY is shorter than 32 bytes.
# In dev/test we already guarantee a 32+ byte sentinel above; in non-dev envs
# the guard at the bottom of this module raises if the key is short. The
# warning still appears in CI for legacy configs — explicit assertion above
# keeps test output clean.

# Development default lists both localhost and 127.0.0.1 so that browser
# probes from either hostname succeed without a CORS preflight failure.
# Override with CORS_ALLOWED_ORIGINS in staging/prod.
CORS_ALLOWED_ORIGINS = [
    o for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:4111,http://127.0.0.1:4111",
    ).split(",") if o
]

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# LLM pricing strictness — when True (default), an LLM call against a model
# with no PRICING entry raises rather than silently recording $0.00. Tests
# and CI should keep this True. See hedgefund_agents/llm/pricing.py.
LLM_REQUIRE_KNOWN_PRICES = os.environ.get("LLM_REQUIRE_KNOWN_PRICES", "1") == "1"

# LLM default preset — the per-agent model map applied to a portfolio strategy
# that has not deliberately chosen one. Production keeps "hybrid" (frontier
# models on the risk/PM/CIO decision path). The dev settings module overrides
# this to "dev" so local development routes every agent to cheap OpenRouter
# models and never erodes the Anthropic budget. Presets are defined in
# apps/models_catalog/presets.py.
LLM_DEFAULT_PRESET = os.environ.get("LLM_DEFAULT_PRESET", "hybrid")

# Hard block on Anthropic API use. When True:
#   1. `AnthropicClient.__init__` raises before any HTTP call.
#   2. `DEFAULT_MODELS` in hedgefund_agents.registry swaps every persona/decision
#      agent off Haiku and onto an OpenRouter route.
# Dev settings force this on so an incomplete per-agent override map (which
# falls through to DEFAULT_MODELS for missing agents) can never leak spend.
BLOCK_ANTHROPIC = os.environ.get("BLOCK_ANTHROPIC", "0") == "1"

# Restrict every model selector in the UI to OpenRouter :free models. When True,
# `/api/models/` marks non-free rows as `available: false` so every dropdown
# (Settings → Models, backtest config, runs, news sentiment) auto-disables the
# paid options. Pairs with BLOCK_ANTHROPIC for zero-spend dev environments.
LLM_FREE_ONLY = os.environ.get("LLM_FREE_ONLY", "0") == "1"

# External providers
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "AIHedgeFund Research example@example.com"
)
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# P3a-2: IBKR Client Portal Gateway URLs. The gateway is a docker-compose
# sidecar (`ibkr-gateway` service) reached two different ways:
#   - The Django app / Celery workers reach it over the compose network at
#     IBKR_GATEWAY_BASE_URL — the IBKR adapter prepends /v1/api to this.
#   - The user's browser reaches the gateway login page at
#     IBKR_GATEWAY_LOGIN_URL for the one-time interactive 2FA. The connect
#     wizard fetches this URL via GET /api/broker-accounts/ibkr/runtime-config/
#     and surfaces it as a clickable button — the user never types it.
# The defaults match the single-machine self-host topology; split-host
# deployments override via infra/docker-compose.override.yml or env vars.
# Recorded in ADR 0011 (gateway sidecar with loopback publishing).
IBKR_GATEWAY_BASE_URL = os.environ.get(
    "IBKR_GATEWAY_BASE_URL", "https://ibkr-gateway:5000",
)
IBKR_GATEWAY_LOGIN_URL = os.environ.get(
    "IBKR_GATEWAY_LOGIN_URL", "https://localhost:5000",
)

# P3a-3: TradeStation Web API (/v3) + OAuth 2.0 authorization-code. BYO
# developer-app credentials per ADR 0012 — the deployer registers their own
# TradeStation app and supplies these env vars. Empty defaults make the
# adapter import safely; the connect wizard surfaces the "no creds" state
# with a setup guide. Hosts are environment-routed by mode (paper → SIM,
# live → production).
TRADESTATION_CLIENT_ID = os.environ.get("TRADESTATION_CLIENT_ID", "")
TRADESTATION_CLIENT_SECRET = os.environ.get("TRADESTATION_CLIENT_SECRET", "")
TRADESTATION_REDIRECT_URI = os.environ.get(
    "TRADESTATION_REDIRECT_URI",
    "http://localhost:8811/api/broker-accounts/oauth/callback/",
)
TRADESTATION_AUTHORIZE_URL = os.environ.get(
    "TRADESTATION_AUTHORIZE_URL", "https://signin.tradestation.com/authorize",
)
TRADESTATION_TOKEN_URL = os.environ.get(
    "TRADESTATION_TOKEN_URL", "https://signin.tradestation.com/oauth/token",
)
TRADESTATION_API_BASE_SIM = os.environ.get(
    "TRADESTATION_API_BASE_SIM", "https://sim-api.tradestation.com/v3",
)
TRADESTATION_API_BASE_LIVE = os.environ.get(
    "TRADESTATION_API_BASE_LIVE", "https://api.tradestation.com/v3",
)
TRADESTATION_SCOPES = "openid offline_access ReadAccount Trade"

# P2n: BYOK gate for paid data providers (FMP, Tiingo). When False, the
# resolver in apps.data.providers.factory refuses to fall back to the env var
# and raises with an actionable error pointing the user at /settings/models.
# FRED is exempt — it's free public-data per data-licensing.md.
ALLOW_PLATFORM_DATA_KEYS = os.environ.get("ALLOW_PLATFORM_DATA_KEYS", "0") == "1"

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_secrets": {"()": "hedgefund.logging_filters.RedactSecretsFilter"},
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["redact_secrets"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}


# --- Secret-safety guard ------------------------------------------------
# In any environment that is not explicitly `dev` or `test`, refuse to start
# with insecure default secrets. Dev defaults stay convenient but visibly
# unsafe; prod-ish environments must override them.
_DJANGO_ENV = os.environ.get("DJANGO_ENV", "dev").lower()
_INSECURE_SECRET_SENTINELS = {
    "",
    "dev-insecure-change-me",
    "change-me",
    "insecure",
    _DEV_INSECURE_SENTINEL,
}

if _DJANGO_ENV not in {"dev", "test"}:
    if SECRET_KEY in _INSECURE_SECRET_SENTINELS:
        raise RuntimeError(
            "DJANGO_SECRET_KEY is still the insecure default; set a unique value "
            f"before starting in DJANGO_ENV={_DJANGO_ENV!r}."
        )
    _jwt_key = SIMPLE_JWT["SIGNING_KEY"]
    if _jwt_key in _INSECURE_SECRET_SENTINELS:
        raise RuntimeError(
            "JWT_SIGNING_KEY is still the insecure default; set a unique value "
            f"before starting in DJANGO_ENV={_DJANGO_ENV!r}."
        )
    if len(_jwt_key.encode("utf-8")) < 32:
        raise RuntimeError(
            "JWT_SIGNING_KEY must be at least 32 bytes "
            f"(got {len(_jwt_key.encode('utf-8'))}) in DJANGO_ENV={_DJANGO_ENV!r}."
        )
