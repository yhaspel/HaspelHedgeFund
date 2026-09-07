"""Telegram delivery via the Bot API (no SDK — a single httpx POST).

The user supplies a per-channel ``bot_token`` + ``chat_id`` (a private chat,
group, or channel id). See ``guides/telegram-setup.md`` for how to obtain them
from @BotFather + @userinfobot. Returns ``(ok, error)`` — never raises.
"""
from __future__ import annotations

import logging

import httpx
from django.conf import settings

log = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _escape_markdown(text: str) -> str:
    # Telegram legacy Markdown: escape the small set that breaks parsing.
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def send_telegram(
    channel, subject: str, body: str, html_body: str | None = None
) -> tuple[bool, str]:
    from hedgefund.offline import is_offline

    if is_offline():  # P4-OFF: external delivery paused at L1
        return False, "skipped: offline"
    cfg = channel.config or {}
    token = channel.get_secret("bot_token")  # Fernet-decrypted at read time
    chat_id = cfg.get("chat_id")
    if not token or not chat_id:
        return False, "missing bot_token or chat_id"

    text = f"*{_escape_markdown(subject)}*\n\n{_escape_markdown(body)}"
    # The bot token is part of the URL PATH, so the URL is itself a credential:
    # it must never be logged, put in an exception message, or handed to
    # anything that logs its argument. Everything below reports the chat id and
    # the HTTP status only. (httpx's own INFO request line is silenced in
    # settings.LOGGING, with a bot-token rule in RedactSecretsFilter behind it.)
    url = f"{settings.TELEGRAM_API_BASE}/bot{token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text[:4096],  # Telegram hard message cap
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        # str(exc) on a transport error can embed the request URL — report the
        # exception TYPE only.
        log.warning("telegram send failed chat_id=%s error=%s", chat_id, type(exc).__name__)
        return False, f"{type(exc).__name__}: connection error"

    log.info("telegram send chat_id=%s status=%s", chat_id, resp.status_code)
    if resp.status_code == 200:
        try:
            ok = bool(resp.json().get("ok"))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return True, ""
    # The body ("Bad Request: chat not found") is what makes this actionable;
    # scrub it anyway — it is persisted on NotificationEvent.error and returned
    # by the API, where the log filter can never reach it.
    from hedgefund.logging_filters import scrub_secrets

    return False, f"telegram HTTP {resp.status_code}: {scrub_secrets(resp.text[:200])}"
