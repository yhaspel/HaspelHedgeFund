"""Resend HTTP-API email backend.

Routes Django's ``EmailMessage.send()`` through Resend's REST API
(https://resend.com/docs/api-reference/emails/send-email) instead of SMTP.
Selected via ``EMAIL_BACKEND=apps.notifications.backends.ResendEmailBackend``.

``channels/email.py`` builds an ``EmailMultiAlternatives`` and calls ``.send()``;
this backend serializes each message to Resend's JSON shape and POSTs it. On a
non-2xx response it raises unless ``fail_silently`` is set, so the notifications
layer surfaces Resend's error (e.g. an unverified ``from`` domain, or the
test-sender's "own address only" rule) in ``NotificationEvent.delivery_status``.
"""
from __future__ import annotations

import httpx
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

RESEND_API_URL = "https://api.resend.com/emails"


class ResendEmailBackend(BaseEmailBackend):
    def __init__(self, *, api_key: str | None = None, fail_silently: bool = False, **kwargs) -> None:
        super().__init__(fail_silently=fail_silently, **kwargs)
        # api_key lets a caller thread a per-user BYOK key via
        # get_connection(api_key=...); None falls back to the platform key.
        resolved = api_key if api_key is not None else getattr(settings, "RESEND_API_KEY", "")
        self.api_key = resolved or ""

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0
        if not self.api_key:
            if not self.fail_silently:
                raise RuntimeError("RESEND_API_KEY is not configured")
            return 0
        sent = 0
        with httpx.Client(timeout=30.0) as client:
            for message in email_messages:
                if self._send(client, message):
                    sent += 1
        return sent

    def _send(self, client: httpx.Client, message) -> bool:
        try:
            resp = client.post(
                RESEND_API_URL,
                json=self._payload(message),
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"Resend {resp.status_code}: {resp.text[:300]}",
                    request=resp.request,
                    response=resp,
                )
        except Exception:
            if not self.fail_silently:
                raise
            return False
        return True

    @staticmethod
    def _payload(message) -> dict:
        body: dict[str, object] = {
            "from": message.from_email,
            "to": list(message.to),
            "subject": message.subject,
            "text": message.body or "",
        }
        if message.cc:
            body["cc"] = list(message.cc)
        if message.bcc:
            body["bcc"] = list(message.bcc)
        if message.reply_to:
            body["reply_to"] = list(message.reply_to)
        # EmailMultiAlternatives attaches the HTML part here → Resend `html`.
        for content, mimetype in getattr(message, "alternatives", None) or []:
            if mimetype == "text/html":
                body["html"] = content
                break
        return body
