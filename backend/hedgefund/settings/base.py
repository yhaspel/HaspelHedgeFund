import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Dev/test sentinels are intentionally insecure but 32+ bytes long so
# SimpleJWT does not emit "key shorter than 32 bytes" warnings on every test
# run. The guard at the bottom of this module still raises for non-dev envs.
_DEV_INSECURE_SENTINEL = "dev-insecure-not-for-prod-not-for-prod-32b-sentinel"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _DEV_INSECURE_SENTINEL)

# ADR 0022: field encryption is decoupled from SECRET_KEY. When unset, crypto.py
# falls back to the legacy SECRET_KEY-derived key (dev convenience); the guard at
# the bottom of this module requires a real value in non-dev/test envs. Generate:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # P3b: tsvector search lookups (Postgres FTS)
    "rest_framework",
    # Revocation list for rotated/logged-out refresh tokens (see SIMPLE_JWT
    # below and POST /api/auth/logout/). Ships its own migrations.
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_celery_beat",
    "apps.accounts",
    "apps.data",
    "apps.runs",
    "apps.backtests",
    "apps.models_catalog",
    "apps.portfolios",
    "apps.screener",
    "apps.watchlists",
    "apps.notifications",
    "apps.schedules",
    "apps.leaderboard",
    "apps.investor_profile",
    "apps.persona_evolution",
    "apps.brokers",
    "apps.graphs",
    "hedgefund_agents",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    # P5-SH WS2.1: bind request_id early so every log line while handling the
    # request (including downstream middleware) is greppable.
    "hedgefund.middleware.RequestIdMiddleware",
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
    # The browsable API renders every view's docstring + a live HTML form for
    # any endpoint the caller can reach; it is a debugging aid, never a
    # production surface. JSON only unless DEBUG is explicitly on.
    "DEFAULT_RENDERER_CLASSES": (
        ["rest_framework.renderers.JSONRenderer",
         "rest_framework.renderers.BrowsableAPIRenderer"]
        if DEBUG
        else ["rest_framework.renderers.JSONRenderer"]
    ),
    # No global throttle (a per-run analysis endpoint must not be rate limited).
    # Only the credential endpoints opt in, via ScopedRateThrottle + these
    # scopes — see apps/accounts/views.py and apps/notifications/views.py.
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "auth": os.environ.get("THROTTLE_RATE_AUTH", "10/min"),
        "notif_test": os.environ.get("THROTTLE_RATE_NOTIF_TEST", "5/min"),
    },
}

SIMPLE_JWT = {
    "SIGNING_KEY": os.environ.get("JWT_SIGNING_KEY", SECRET_KEY),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    # Rotate the refresh token on every /auth/refresh/ call so an actively-used
    # session never expires: each refresh issues a fresh 7-day refresh token,
    # giving a sliding window. A user is only logged out after 7 days of zero
    # activity (or on explicit logout).
    "ROTATE_REFRESH_TOKENS": True,
    # ... and the token it replaced is revoked immediately (token_blacklist),
    # so a stolen refresh token stops working the moment the legitimate client
    # next refreshes, instead of staying valid for its full 7-day life. This is
    # also what makes POST /api/auth/logout/ a real logout.
    "BLACKLIST_AFTER_ROTATION": True,
}

# Django's four stock validators. Minimum length 10 (Django's default 8 is below
# every current guideline) — the signup serializer runs them explicitly.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# SimpleJWT emits a UserWarning when SIGNING_KEY is shorter than 32 bytes.
# In dev/test we already guarantee a 32+ byte sentinel above; in non-dev envs
# the guard at the bottom of this module raises if the key is short. The
# warning still appears in CI for legacy configs — explicit assertion above
# keeps test output clean.

# P12/D2: self-service signup is open by default (self-hosters see no change);
# a single-user instance locks it down with SIGNUP_ENABLED=0, which makes
# POST /api/auth/signup/ return 403. The only access-control surface this phase
# adds — no allowlist models, no admin UI. See ADR 0031.
SIGNUP_ENABLED = os.environ.get("SIGNUP_ENABLED", "1") == "1"

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

# Phase 8 Lane-B E2E (ADR 0019). Off by default everywhere; only the dev/test
# settings flip the seed guard on, and the nightly Lane-B job exports the stub
# env vars. E2E_STUB_LLM short-circuits every LLM call to a deterministic stub
# (registry.get_llm); E2E_STUB_BROKER keeps broker actions on the instant Demo
# fill path. E2E_SEED_ALLOWED gates `manage.py seed_e2e` (refuses in prod).
E2E_STUB_LLM = os.environ.get("E2E_STUB_LLM", "0") == "1"
E2E_STUB_BROKER = os.environ.get("E2E_STUB_BROKER", "0") == "1"
E2E_SEED_ALLOWED = False

# Restrict every model selector in the UI to OpenRouter :free models. When True,
# `/api/models/` marks non-free rows as `available: false` so every dropdown
# (Settings → Models, backtest config, runs, news sentiment) auto-disables the
# paid options. Pairs with BLOCK_ANTHROPIC for zero-spend dev environments.
LLM_FREE_ONLY = os.environ.get("LLM_FREE_ONLY", "0") == "1"

# Opt-in paid escape hatch for the OpenRouter adapter. When True, a :free route
# whose 429 retries AND same-tier free fallbacks are all exhausted hops once to
# the cheap paid analytical default (meta-llama/llama-3.3-70b-instruct, non-:free)
# instead of raising RateLimited and aborting the run. Default off so dev /
# zero-spend environments (which pair this with BLOCK_ANTHROPIC) never silently
# bill when the shared free pool is saturated. Kept independent of BLOCK_ANTHROPIC
# so flipping it on can't defeat that guard.
OPENROUTER_PAID_FALLBACK = os.environ.get("OPENROUTER_PAID_FALLBACK", "0") == "1"

# --- Self-healing run resilience (see hedgefund_agents/llm/adapters/openrouter.py
# and hedgefund_agents/graphs/_node_fallback.py) -------------------------------
# A run must never hang indefinitely or die because one of ~16 agents drew a
# dead/slow/misbehaving model route. Three composing layers:
#
#   L1  LLM_HTTP_READ_TIMEOUT — per-read socket timeout (seconds). The old scalar
#       httpx timeout of 120s is a per-OPERATION read timeout; a reasoning route
#       that trickles bytes resets it on every byte and never fires (run 236 hung
#       10+ min). A tighter read timeout converts a stalled response into a normal
#       httpx.ReadTimeout the retry/self-heal path can act on.
#   L2  LLM_SELF_HEAL + LLM_MAX_MODEL_FALLBACKS + LLM_LAST_RESORT_MODEL — when a
#       route is permanently dead (HTTP 404/402, run 228-235), returns a
#       non-JSON body (run 241), is terminally empty (reasoning exhaustion,
#       run 236/237), or exhausts its transient-retry budget, the adapter walks
#       a catalog-driven SAME-TIER fallback chain (P13): up to
#       LLM_MAX_MODEL_FALLBACKS other active models from the failed model's own
#       tier menu (:free routes chain within :free, paid within paid), ending at
#       the known-good NON-reasoning last resort — instead of failing the run.
#   L3  RUN_SOFT_TIME_LIMIT_SECONDS / RUN_HARD_TIME_LIMIT_SECONDS — Celery
#       wall-clock cap on execute_run / run_candidate_council so a run is
#       guaranteed to terminate (soft → clean FAILED; hard → SIGKILL backstop).
#
# P13 adds a fourth, selection-time layer ABOVE L1: every consumer of a
# per-agent model map (preset endpoint, run submission, dispatch, execution
# seams, the stored-map doctor in reconcile_model_catalog) heals dead ids to
# live same-tier models via apps.models_catalog.tier_menus.heal_overrides — so
# the adapter chain is the last net, not the first responder.
LLM_HTTP_READ_TIMEOUT = float(os.environ.get("LLM_HTTP_READ_TIMEOUT", "45"))
LLM_SELF_HEAL = os.environ.get("LLM_SELF_HEAL", "1") == "1"
# P13: max number of FALLBACK models one agent call may try after its configured
# model fails (each with its own retry budget). 5 ≈ the whole frugal/dev menu.
LLM_MAX_MODEL_FALLBACKS = int(os.environ.get("LLM_MAX_MODEL_FALLBACKS", "5"))
# Non-reasoning, proven prod analytical default. Under LLM_FREE_ONLY the adapter
# uses the :free variant so zero-spend environments never silently bill.
LLM_LAST_RESORT_MODEL = os.environ.get(
    "LLM_LAST_RESORT_MODEL", "meta-llama/llama-3.3-70b-instruct"
)
# When True (default), a live run tolerates a single agent's unrecoverable
# failure by emitting that agent's null signal (the council proceeds and the run
# completes "done", flagged degraded) instead of aborting the whole run. Genuine
# config errors (ModelUnavailable after the L2 hop) still raise so a fully-broken
# setup surfaces. Backtests have always behaved this way; this extends it to live.
RUN_SELF_HEAL = os.environ.get("RUN_SELF_HEAL", "1") == "1"
RUN_SOFT_TIME_LIMIT_SECONDS = int(os.environ.get("RUN_SOFT_TIME_LIMIT_SECONDS", "600"))
RUN_HARD_TIME_LIMIT_SECONDS = int(os.environ.get("RUN_HARD_TIME_LIMIT_SECONDS", "720"))
# P5-SH WS1.2: default mid-run LLM-spend cap (USD) for ad-hoc/scheduled runs
# whose own Run.max_budget_usd is NULL. Unset ⇒ None ⇒ guard off (ship safe:
# no surprise aborts). Set e.g. RUN_DEFAULT_MAX_BUDGET_USD=5 to backstop every
# run instance-wide. Enforced between agent nodes in record_llm_call.
_run_budget = os.environ.get("RUN_DEFAULT_MAX_BUDGET_USD", "").strip()
RUN_DEFAULT_MAX_BUDGET_USD = Decimal(_run_budget) if _run_budget else None

# External providers
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "AIHedgeFund Research example@example.com"
)
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY", "")

# Ollama host for local models. The adapter and per-user ProviderKey.ollama_host
# already default to this; naming it here lets the health probe + offline
# resolver share one source of truth (the view is unauthenticated, so it has no
# per-user host to read).
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# The deployed commit, reported by /api/health/ as "build" so a deploy can be
# verified positively instead of by inference. Railway sets this for
# GitHub-triggered deploys; empty locally and in CI.
BUILD_SHA = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")

# --- Offline mode (P4-OFF, ADR 0029) -----------------------------------------
# OFFLINE_MODE=1 puts the backend in "internet down, local stack up" (L1) mode:
#   1. External data providers are fenced at their HTTP seam (apps/data/
#      providers/_http.py) — reads serve last-persisted DB rows, never the WAN.
#   2. Every LLM call is forced onto the local Ollama model at the execution
#      seams, and registry.get_llm hard-blocks any non-Ollama adapter (R3).
#   3. Outward-facing periodic tasks (provider refresh, notifications, broker
#      polling) no-op fast instead of spamming connection errors every beat.
# Explicit env flag, never auto-detected (deterministic + testable, D5).
OFFLINE_MODE = os.environ.get("OFFLINE_MODE", "0") == "1"
# The Ollama tag every agent runs on offline. Kept inside the 24 GB M4 envelope
# (§WS-5): qwen2.5:7b is the live-verified default; >14B is excluded by docs.
OFFLINE_LLM_MODEL = os.environ.get("OFFLINE_LLM_MODEL", "qwen2.5:7b")

# P10 §E3 — the news-sentiment lab's FROZEN classifier. The lab tasks always
# classify with this model (never the user's news-page preference): swapping
# the sentiment model mid-experiment would silently change the sleeve's signal
# definition and invalidate the forward test. Change only between experiments.
NEWS_LAB_SENTIMENT_MODEL = os.environ.get(
    "NEWS_LAB_SENTIMENT_MODEL", "openrouter:qwen/qwen3.6-27b"
)

# P7 / P14 — Autonomous fund bootstrap. The fund trades ONE shared Alpaca paper
# account, configured as a single UNNUMBERED ``ALPACA_PAPER_{NAME,KEY_ID,SECRET}``
# triple. The numbered ``ALPACA_PAPER_{1,2,3}_*`` slots are retired: P14 made the
# fund a shared pool with per-strategy sleeves, so a second/third account no
# longer has a role. NAME is a human label that becomes the BrokerAccount.label.
# ``bootstrap_autonomous_fund`` reads this (paper-only) + the owner email. The
# empty defaults keep a default install importable and create nothing; running
# the command without a complete triple aborts with a CommandError rather than
# silently doing nothing. .env stays gitignored.
#
# Deliberately still a LIST OF ONE keyed by ``slot``: the bootstrap command
# iterates triples, keys accounts by slot and exposes ``--slot``, so preserving
# the shape keeps that logic — and the tests that inject it — untouched.
ALPACA_PAPER_ACCOUNTS = [
    {
        "slot": 1,
        "name": os.environ.get("ALPACA_PAPER_NAME", ""),
        "key_id": os.environ.get("ALPACA_PAPER_KEY_ID", ""),
        "secret": os.environ.get("ALPACA_PAPER_SECRET", ""),
    }
]
ALPACA_FUND_OWNER_EMAIL = os.environ.get("ALPACA_FUND_OWNER_EMAIL", "")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# The default cache backend is LocMemCache, which is PER PROCESS. Two things
# depend on the cache being SHARED across processes: the operator-alert
# strike/cooldown counters (apps/notifications/operator.py — under
# `--concurrency=N` every worker child would otherwise keep its own count and
# never reach the alert threshold) and the auth/test-send rate throttles (a
# gunicorn worker each). Redis is already a hard dependency (Celery broker), so
# use it when REDIS_URL is configured and fall back to LocMemCache only for a
# bare `manage.py` invocation with no Redis at all.
CACHES = {
    "default": (
        {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "hf",
        }
        if REDIS_URL
        else {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hedgefund-locmem",
        }
    )
}

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
# and raises with an actionable error pointing the user at /settings/providers.
# FRED is exempt — it's free public-data per data-licensing.md.
ALLOW_PLATFORM_DATA_KEYS = os.environ.get("ALLOW_PLATFORM_DATA_KEYS", "0") == "1"

# P4 13F: enrich the Fundamentals agent with institutional-ownership data.
FUNDAMENTALS_USE_13F = os.environ.get("FUNDAMENTALS_USE_13F", "1") == "1"

# P4c: user-composed agent graphs. Defaults preserve today's behavior (every
# run uses the hardcoded council.py) until the editor ships.
#   ENABLE_DB_GRAPHS                 master switch for resolving a run/backtest's
#                                    graph from its AgentGraphVersion FK.
#   GRAPH_FALLBACK_TO_HARDCODED      on compile failure, fall back to council.py
#                                    and log instead of failing the run.
#   BLOCK_INVALID_GRAPH_AT_SUBMISSION reject submissions whose selected version
#                                    isn't validation_status="valid".
ENABLE_DB_GRAPHS = os.environ.get("ENABLE_DB_GRAPHS", "0") == "1"
GRAPH_FALLBACK_TO_HARDCODED = os.environ.get("GRAPH_FALLBACK_TO_HARDCODED", "1") == "1"
BLOCK_INVALID_GRAPH_AT_SUBMISSION = os.environ.get("BLOCK_INVALID_GRAPH_AT_SUBMISSION", "1") == "1"

# P3b: email + notification delivery. Dev defaults to the console backend so
# scheduled-run notifications are visible in the worker logs without an SMTP
# server; staging/prod override EMAIL_BACKEND + EMAIL_HOST via env. The test
# settings module swaps to the locmem backend so delivery is assertable.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "0") == "1"
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "Hedge Fund <hedgefund@localhost>"
)
# Resend HTTP-API key, consumed by apps.notifications.backends.ResendEmailBackend
# when EMAIL_BACKEND points at it. Empty in dev unless set in .env.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

# P3b: per-user/day cap on scheduled-run notifications (anti-fatigue, plan
# risk #1). Counts NotificationEvent rows created in the trailing 24h.
NOTIFICATIONS_MAX_PER_DAY = int(os.environ.get("NOTIFICATIONS_MAX_PER_DAY", "10"))
# Per-ticker/day cap so one noisy name can't dominate the daily budget, and the
# digest threshold: when a single fire surfaces more than this many material
# events, send one consolidated digest instead of N messages (plan refinement #4).
NOTIFICATIONS_MAX_PER_TICKER_PER_DAY = int(
    os.environ.get("NOTIFICATIONS_MAX_PER_TICKER_PER_DAY", "3")
)
NOTIFICATIONS_DIGEST_THRESHOLD = int(os.environ.get("NOTIFICATIONS_DIGEST_THRESHOLD", "5"))

# P3b: global kill switch for scheduled-run paper auto-submit. Even with a
# schedule's auto_paper_submit=True, no orders are created when this is off.
# Live auto-submit is *permanently* impossible (the confirmation gate hard-blocks
# scheduled_job × live) — this only gates the paper path.
PAPER_AUTO_SUBMIT_ENABLED = os.environ.get("PAPER_AUTO_SUBMIT_ENABLED", "1") == "1"

# P3b: Telegram Bot API base. Overridable so tests can point delivery at a
# respx-mocked host. The user supplies a per-channel bot_token + chat_id; see
# guides/telegram-setup.md.
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

# --- Wave-3 review settings (2026-09-07 adversarial review) -------------------
# News sentiment/translation are BYOK-only: without the user's own OpenRouter
# key the feature is skipped and the feed still renders. Flip this on only if
# you accept that every account on the instance spends the operator's key.
ALLOW_PLATFORM_LLM_FOR_NEWS = os.environ.get("ALLOW_PLATFORM_LLM_FOR_NEWS", "0") == "1"
# Per-user daily ceiling on news-LLM spend (USD). Over it, scoring/translation
# is skipped with a reason — never raised.
NEWS_LLM_DAILY_CAP_USD = Decimal(os.environ.get("NEWS_LLM_DAILY_CAP_USD", "0.50"))
# Screener stage 2 enriches from cached DailyBar rows and lazily fetches at most
# this many uncached tickers per run (a cold run used to burst ~604 FMP calls
# against the same key the live pods depend on). 0 = DB only.
SCREENER_LAZY_FILL_MAX = int(os.environ.get("SCREENER_LAZY_FILL_MAX", "40"))
# Broker codes a user may connect. IBKR (P3a-2) and TradeStation (P3a-3) are
# deferred phases: their adapters exist but were never validated end to end, so
# the registry marks them deferred and account creation is refused. Add a code
# here to re-enable one.
ENABLED_BROKERS = [
    code.strip()
    for code in os.environ.get("ENABLED_BROKERS", "alpaca_paper,demo").split(",")
    if code.strip()
]
# Annual stock-borrow fee charged daily on |short notional| by backtest engine
# v2, on top of Backtest.financing_bps. Shorts used to be financed for free.
BACKTEST_SHORT_BORROW_BPS = Decimal(os.environ.get("BACKTEST_SHORT_BORROW_BPS", "50"))

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_secrets": {"()": "hedgefund.logging_filters.RedactSecretsFilter"},
        # P5-SH WS2.1: stamp request_id / run_id onto every record.
        "context": {"()": "hedgefund.logging_filters.ContextFilter"},
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(request_id)s %(run_id)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["context", "redact_secrets"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # httpx logs EVERY request line at INFO, including the full URL. Some of
        # those URLs carry credentials in the PATH (Telegram's
        # /bot<token>/sendMessage) where a query-param scrub cannot reach them.
        # The redact filter has a bot-token rule as a second line of defence,
        # but the request log itself has no operational value here — keep it at
        # WARNING so the secret is never rendered in the first place.
        "httpx": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "httpcore": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# --- Optional Sentry (P5-SH WS2.3) --------------------------------------
# Default OFF. Unset ⇒ no import, no init on any runtime path (web/worker/CLI).
# Set SENTRY_DSN and install the extra (`uv sync --extra sentry`) to opt in.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    from hedgefund.observability import init_sentry

    init_sentry(SENTRY_DSN, environment=os.environ.get("DJANGO_ENV", ""))


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
    if not FIELD_ENCRYPTION_KEY.strip():
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY is not set (ADR 0022); stored BYO keys and "
            "broker credentials would fall back to the SECRET_KEY-derived key. "
            "Set a dedicated value — generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` — in "
            f"DJANGO_ENV={_DJANGO_ENV!r}."
        )
    else:
        # Presence is not enough: a malformed value (wrong length, hex, a reused
        # token_urlsafe secret) passes as truthy, then bricks crypto at runtime —
        # decrypt() would swallow the Fernet ValueError and read every stored key
        # as absent. Validate constructibility here so it fails fast and clearly.
        from cryptography.fernet import Fernet

        try:
            Fernet(FIELD_ENCRYPTION_KEY.strip().encode())
        except Exception as exc:
            raise RuntimeError(
                "FIELD_ENCRYPTION_KEY is set but is not a valid Fernet key "
                "(needs urlsafe-base64 of 32 bytes). Generate one with "
                "`python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`."
            ) from exc
