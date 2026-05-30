"""Email delivery. Uses Django's configured EMAIL_BACKEND (console in dev,
locmem in tests, SMTP in prod). Returns ``(ok, error)`` — never raises."""
from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


def send_email(channel, subject: str, body: str, html_body: str | None = None) -> tuple[bool, str]:
    address = (channel.config or {}).get("address") or getattr(channel.user, "email", "")
    if not address:
        return False, "no email address configured"
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[address],
        )
        if html_body:
            msg.attach_alternative(html_body, "text/html")
        msg.send()
        return True, ""
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the run
        return False, f"{type(exc).__name__}: {exc}"[:300]
