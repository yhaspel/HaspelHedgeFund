from .base import *  # noqa: F401,F403

DEBUG = True

# P2n: dev keeps platform-key fallback enabled so local workflows that rely on
# env vars (FMP_API_KEY etc.) continue to work without per-user setup.
ALLOW_PLATFORM_DATA_KEYS = True
