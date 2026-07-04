"""Telegram delivery via the Bot API (no SDK — a single httpx POST).

The user supplies a per-channel ``bot_token`` + ``chat_id`` (a private chat,
group, or channel id). See ``guides/telegram-setup.md`` for how to obtain them
from @BotFather + @userinfobot. Returns ``(ok, error)`` — never raises.
"""
from __future__ import annotations

import httpx
from django.conf import settings

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
    token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id")
    if not token or not chat_id:
        return False, "missing bot_token or chat_id"

    text = f"*{_escape_markdown(subject)}*\n\n{_escape_markdown(body)}"
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
        return False, f"{type(exc).__name__}: {exc}"[:300]

    if resp.status_code == 200:
        try:
            ok = bool(resp.json().get("ok"))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return True, ""
    return False, f"telegram HTTP {resp.status_code}: {resp.text[:200]}"
