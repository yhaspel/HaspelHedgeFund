"""Email delivery. Uses Django's configured EMAIL_BACKEND (console in dev,
locmem in tests, SMTP in prod). Returns ``(ok, error)`` — never raises."""
from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection


def _connection_for(user):
    """BYOK: when Resend is the active backend, send through the channel
    owner's own Resend key (falling back to the platform key). Returns None
    for other backends (console/SMTP), so Django uses its default connection."""
    if not settings.EMAIL_BACKEND.endswith("ResendEmailBackend"):
        return None
    from apps.notifications.byok import resolve_resend_api_key

    return get_connection(api_key=resolve_resend_api_key(user))


def send_email(channel, subject: str, body: str, html_body: str | None = None) -> tuple[bool, str]:
    from hedgefund.offline import is_offline

    if is_offline():  # P4-OFF: external delivery paused at L1
        return False, "skipped: offline"
    address = (channel.config or {}).get("address") or getattr(channel.user, "email", "")
    if not address:
        return False, "no email address configured"
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[address],
            connection=_connection_for(channel.user),
        )
        if html_body:
            msg.attach_alternative(html_body, "text/html")
        msg.send()
        return True, ""
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the run
        return False, f"{type(exc).__name__}: {exc}"[:300]
