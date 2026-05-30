"""Unit tests for the Resend HTTP-API email backend.

The backend serializes Django EmailMessages to Resend's JSON shape and POSTs
them; these tests mock httpx so no real network call is made.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.core.mail import EmailMultiAlternatives
from django.test import override_settings

from apps.notifications.backends import RESEND_API_URL, ResendEmailBackend


def _resp(status_code: int = 200, text: str = '{"id":"abc"}') -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.request = MagicMock()
    return r


def _msg() -> EmailMultiAlternatives:
    m = EmailMultiAlternatives(
        subject="Hi",
        body="plain body",
        from_email="Hedge Fund <onboarding@resend.dev>",
        to=["you@example.com"],
    )
    m.attach_alternative("<b>rich</b>", "text/html")
    return m


@override_settings(RESEND_API_KEY="re_test")
def test_send_posts_resend_payload_and_returns_count():
    client = MagicMock()
    client.post.return_value = _resp()
    client.__enter__.return_value = client
    with patch("apps.notifications.backends.httpx.Client", return_value=client):
        sent = ResendEmailBackend().send_messages([_msg()])
    assert sent == 1
    args, kwargs = client.post.call_args
    assert args[0] == RESEND_API_URL
    assert kwargs["headers"]["Authorization"] == "Bearer re_test"
    body = kwargs["json"]
    assert body["from"] == "Hedge Fund <onboarding@resend.dev>"
    assert body["to"] == ["you@example.com"]
    assert body["subject"] == "Hi"
    assert body["text"] == "plain body"
    assert body["html"] == "<b>rich</b>"  # html alternative surfaced


@override_settings(RESEND_API_KEY="re_test")
def test_non_2xx_raises_when_not_fail_silently():
    client = MagicMock()
    client.post.return_value = _resp(status_code=403, text="domain not verified")
    client.__enter__.return_value = client
    with patch("apps.notifications.backends.httpx.Client", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            ResendEmailBackend(fail_silently=False).send_messages([_msg()])


@override_settings(RESEND_API_KEY="re_test")
def test_non_2xx_swallowed_when_fail_silently():
    client = MagicMock()
    client.post.return_value = _resp(status_code=403, text="nope")
    client.__enter__.return_value = client
    with patch("apps.notifications.backends.httpx.Client", return_value=client):
        sent = ResendEmailBackend(fail_silently=True).send_messages([_msg()])
    assert sent == 0  # failure counted as not-sent, no raise


@override_settings(RESEND_API_KEY="")
def test_missing_key_raises_when_not_fail_silently():
    with pytest.raises(RuntimeError):
        ResendEmailBackend(fail_silently=False).send_messages([_msg()])


def test_empty_message_list_is_noop():
    assert ResendEmailBackend().send_messages([]) == 0
