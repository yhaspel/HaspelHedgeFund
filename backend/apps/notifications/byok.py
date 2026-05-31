"""BYOK resolution for notification delivery.

Mirrors the LLM/data-provider precedence (hedgefund_agents.registry.get_llm):
a user's own encrypted ProviderKey wins, else the platform key from settings.
"""
from __future__ import annotations

from django.conf import settings


def resolve_resend_api_key(user) -> str:
    """Return the Resend API key to send *user*'s notifications with.

    User BYOK key (ProviderKey) if set, else the platform ``RESEND_API_KEY``.
    """
    if user is not None:
        try:
            from apps.models_catalog.models import ProviderKey

            pk = ProviderKey.objects.filter(user=user).first()
        except Exception:
            pk = None
        if pk is not None and pk.has_key("resend"):
            return pk.get_key("resend")
    return getattr(settings, "RESEND_API_KEY", "") or ""
