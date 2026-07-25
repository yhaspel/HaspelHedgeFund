"""Production settings (Railway / any PaaS with edge TLS). ADR 0031."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False

# TLS terminates at the platform edge — never SSL-redirect internally (loops).
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# RAILWAY_PUBLIC_DOMAIN is injected by Railway and carries no scheme.
_front = os.environ.get("FRONTEND_URL", "").rstrip("/")
_rail = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
CSRF_TRUSTED_ORIGINS = [u for u in [_front, f"https://{_rail}" if _rail else ""] if u]
CORS_ALLOWED_ORIGINS = [u for u in [_front] if u]

# WhiteNoise directly after SecurityMiddleware — located by name, not index
# (base MIDDLEWARE has corsheaders at 0 and RequestIdMiddleware at 1).
MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "whitenoise.middleware.WhiteNoiseMiddleware",
)
# Django 5.1 removed STATICFILES_STORAGE — use STORAGES.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
