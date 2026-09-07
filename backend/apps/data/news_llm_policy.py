"""Cost policy for the two News LLM features (sentiment + translation).

WAVE-3 P2 item 1. Before this module both features resolved their client via
``get_llm(provider, user_id=...)``, which silently falls back to
``settings.OPENROUTER_API_KEY`` — so any signed-in account got per-page-view
sentiment *and* translation on the operator's credit, on **any** active catalog
model (frontier + reasoning included), with ``record_llm_call(run_id=None)``
bypassing the run budget guard entirely.

Two gates now sit in front of every news LLM call:

1. **BYOK required.** The user must have their own OpenRouter ``ProviderKey``
   row, unless the operator explicitly opts in with
   ``settings.ALLOW_PLATFORM_LLM_FOR_NEWS = True`` (default ``False``, read
   with ``getattr`` — no settings file is owned this wave).
2. **Per-user daily USD cap** (``settings.NEWS_LLM_DAILY_CAP_USD``, default
   ``0.50``): the sum of today's ``LLMCall`` rows attributed to this user's
   news features. Over the cap the feature is *skipped with a reason* — never
   an exception; the feed always renders.

``LLMCall`` has no user FK (its owners are Run / Backtest / PortfolioTarget and
the news features run under none of them), so :class:`NewsLlmUsage` is the
attribution index: one row per news LLM call, pointing at the ``LLMCall`` it
paid for. The cap therefore sums real ``LLMCall`` rows, per user, per day.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

log = logging.getLogger(__name__)

FEATURE_SENTIMENT = "sentiment"
FEATURE_TRANSLATION = "translation"

_FEATURE_LABEL = {
    FEATURE_SENTIMENT: "Sentiment",
    FEATURE_TRANSLATION: "Translation",
}

DEFAULT_DAILY_CAP_USD = Decimal("0.50")

BYOK_MESSAGE = (
    "{feature} needs your own OpenRouter key — add one at /settings/providers. "
    "The news feed still works without it."
)
CAP_MESSAGE = (
    "{feature} paused: today's news AI budget of ${cap} is spent "
    "(${spent} used). It resets tomorrow."
)


def allow_platform_llm_for_news() -> bool:
    """Operator opt-in to spend the platform OpenRouter key on news."""
    return bool(getattr(settings, "ALLOW_PLATFORM_LLM_FOR_NEWS", False))


def daily_cap_usd() -> Decimal:
    """Per-user daily USD cap for the news LLM features."""
    raw = getattr(settings, "NEWS_LLM_DAILY_CAP_USD", DEFAULT_DAILY_CAP_USD)
    try:
        return Decimal(str(raw))
    except Exception:  # noqa: BLE001 — a bad operator value must not 500 the feed.
        return DEFAULT_DAILY_CAP_USD


def user_has_openrouter_key(user_id: int | None) -> bool:
    """True when this user has stored their own OpenRouter key."""
    if user_id is None:
        return False
    try:
        from apps.models_catalog.models import ProviderKey

        pk = ProviderKey.objects.filter(user_id=user_id).first()
    except Exception:  # noqa: BLE001 — never break the page on a vault error.
        return False
    return bool(pk is not None and pk.has_key("openrouter"))


def spent_today_usd(user_id: int | None) -> Decimal:
    """Sum of today's news-attributed ``LLMCall`` spend for this user."""
    if user_id is None:
        return Decimal("0")
    from .models import NewsLlmUsage

    total = (
        NewsLlmUsage.objects.filter(
            user_id=user_id, created_at__date=timezone.localdate()
        ).aggregate(s=Sum("cost_usd"))["s"]
        or Decimal("0")
    )
    return Decimal(total)


def check_news_llm_allowed(
    user_id: int | None, *, feature: str
) -> tuple[bool, str | None]:
    """``(allowed, reason)`` for one news LLM feature.

    ``reason`` is a short, user-facing sentence for the response payload; the
    caller *skips* the feature, it never raises.
    """
    label = _FEATURE_LABEL.get(feature, "News AI")
    has_key = user_has_openrouter_key(user_id)
    if not has_key and not allow_platform_llm_for_news():
        return False, BYOK_MESSAGE.format(feature=label)
    cap = daily_cap_usd()
    if cap > 0:
        spent = spent_today_usd(user_id)
        if spent >= cap:
            return False, CAP_MESSAGE.format(
                feature=label, cap=f"{cap:.2f}", spent=f"{spent:.2f}"
            )
    return True, None


def record_news_llm_spend(user_id: int | None, *, feature: str, resp, call=None) -> None:
    """Attribute one news LLM call's cost to ``user_id`` for the daily cap.

    Costs every billed attempt the same way ``record_llm_call`` does (a
    structured call can burn up to 3 requests before one parses). Best-effort:
    a failure here must never break the page.
    """
    if user_id is None:
        return
    try:
        from .models import NewsLlmUsage

        attempts = [*getattr(resp, "prior_attempts", []), resp]
        cost = sum(
            (
                Decimal(f"{a.cost_usd:.6f}")
                for a in attempts
                if getattr(a, "cost_usd", 0) and a.cost_usd > 0
            ),
            Decimal("0"),
        )
        NewsLlmUsage.objects.create(
            user_id=user_id,
            llm_call=call,
            feature=feature,
            cost_usd=cost,
        )
    except Exception as exc:  # noqa: BLE001 — accounting must not break the page.
        log.warning("news_llm spend-record error feature=%s err=%s", feature, exc)


def llm_status(user_id: int | None) -> dict:
    """The ``llm_status`` block served by ``/news/feed/`` and
    ``/news/preferences/`` so the UI can explain what is (not) running."""
    has_key = user_has_openrouter_key(user_id)
    platform_ok = allow_platform_llm_for_news()
    cap = daily_cap_usd()
    spent = spent_today_usd(user_id)
    allowed, reason = check_news_llm_allowed(user_id, feature=FEATURE_SENTIMENT)
    return {
        "byok_required": not platform_ok,
        "has_user_key": has_key,
        "platform_key_allowed": platform_ok,
        "allowed": allowed,
        "reason": reason,
        "daily_cap_usd": float(cap),
        "spent_today_usd": float(spent),
        # Without a BYO key the model picker is limited to the frugal preset
        # menu (enforced by ``PUT /news/preferences/``).
        "model_choices_restricted": not has_key,
    }
