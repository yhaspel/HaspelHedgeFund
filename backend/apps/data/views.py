"""Read-only API: macro snapshot + Markov regime + ticker news digest.

Bare-minimum: backend computes/caches; frontend renders.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from decimal import Decimal

from django.utils import timezone
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .cache import cache_get, cache_set
from .models import (
    CompanyProfile,
    DailyBar,
    MacroSnapshot,
    MarketNewsItem,
    NewsItem,
    RegimeSnapshot,
    UserNewsPreferences,
)

log = logging.getLogger(__name__)

# WS-2: live quote-derived metrics (mcap/PE/EPS/price) are short-lived; ~30 min.
_PROFILE_TTL_SECONDS = 30 * 60
# WS-2: cap batch fan-out so a runaway request can't trigger 1000 FMP calls.
_BATCH_MAX_SYMBOLS = 50


def _parse_as_of(request: Request) -> dt.date:
    raw = request.query_params.get("as_of")
    if not raw:
        return dt.date.today()
    return dt.date.fromisoformat(raw)


def _parse_model_type(request: Request) -> str:
    raw = request.query_params.get("model_type") or "labelled_markov"
    return raw if raw in {"labelled_markov", "gaussian_hmm"} else "labelled_markov"


class MacroSnapshotView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        as_of = _parse_as_of(request)
        snap = (
            MacroSnapshot.objects.filter(as_of_date__lte=as_of)
            .order_by("-as_of_date")
            .first()
        )
        if not snap:
            from .tasks import prewarm_macro_snapshot
            prewarm_macro_snapshot(as_of.isoformat())
            snap = MacroSnapshot.objects.filter(as_of_date=as_of).first()
        if not snap:
            return Response({"detail": "no macro snapshot available"}, status=503)
        return Response(
            {
                "as_of_date": snap.as_of_date.isoformat(),
                "growth_quadrant": snap.growth_quadrant,
                "inflation_regime": snap.inflation_regime,
                "yield_curve_state": snap.yield_curve_state,
                "policy_stance": snap.policy_stance,
                "narrative": snap.narrative,
                "sector_implications": snap.sector_implications,
                "series_used": snap.series_used,
                "markov_consensus": snap.markov_consensus,
            }
        )


def _snapshot_payload(snap: RegimeSnapshot) -> dict:
    return {
        "ticker": snap.ticker,
        "as_of_date": snap.as_of_date.isoformat(),
        "model_type": snap.model_type,
        "config_hash": snap.config_hash,
        "last_price_date": snap.last_price_date.isoformat(),
        "current_state": snap.current_state,
        "current_return": snap.current_return,
        "current_state_persistence": snap.current_state_persistence,
        "bull_persistence": snap.bull_persistence,
        "sideways_persistence": snap.sideways_persistence,
        "bear_persistence": snap.bear_persistence,
        "bull_prob_1d": snap.bull_prob_1d,
        "sideways_prob_1d": snap.sideways_prob_1d,
        "bear_prob_1d": snap.bear_prob_1d,
        "bull_prob_5d": snap.bull_prob_5d,
        "sideways_prob_5d": snap.sideways_prob_5d,
        "bear_prob_5d": snap.bear_prob_5d,
        "bull_minus_bear_1d": snap.bull_minus_bear_1d,
        "prior_current_state": snap.prior_current_state,
        "current_state_persistence_delta": snap.current_state_persistence_delta,
        "bull_persistence_delta": snap.bull_persistence_delta,
        "bear_persistence_delta": snap.bear_persistence_delta,
        "state_changed_from_prior": snap.state_changed_from_prior,
        "stale": snap.stale,
    }


class RegimeSnapshotView(APIView):
    """Latest persisted ``RegimeSnapshot`` for one ticker.

    Never refits on demand — read-only against the prewarm-managed table.
    Future ``as_of`` values degrade to the latest available snapshot with
    ``stale=true`` instead of returning an error.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, ticker: str) -> Response:
        from hedgefund_agents.macro.regime_persistence import (
            DEFAULT_STALENESS_DAYS,
            get_latest_snapshot,
        )

        as_of = _parse_as_of(request)
        model_type = _parse_model_type(request)
        snap = get_latest_snapshot(
            ticker.upper(),
            as_of_date=as_of,
            model_type=model_type,
            staleness_days=DEFAULT_STALENESS_DAYS,
        )
        if snap is None:
            return Response(
                {
                    "ticker": ticker.upper(),
                    "as_of": as_of.isoformat(),
                    "snapshot": None,
                    "reason": "no_snapshot",
                }
            )
        return Response(
            {
                "ticker": ticker.upper(),
                "as_of": as_of.isoformat(),
                "snapshot": _snapshot_payload(snap),
            }
        )


class RegimeBatchView(APIView):
    """Batch read of regime snapshots — one entry per requested ticker.

    Missing / failed fits return ``{ticker, snapshot: null, reason}``
    rather than a 5xx so the dashboard can degrade gracefully.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from hedgefund_agents.macro.regime_persistence import (
            DEFAULT_STALENESS_DAYS,
            get_latest_snapshot,
        )

        as_of = _parse_as_of(request)
        model_type = _parse_model_type(request)
        raw = request.query_params.get("tickers") or ""
        tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
        items: list[dict] = []
        for ticker in tickers:
            snap = get_latest_snapshot(
                ticker,
                as_of_date=as_of,
                model_type=model_type,
                staleness_days=DEFAULT_STALENESS_DAYS,
            )
            if snap is None:
                items.append(
                    {"ticker": ticker, "snapshot": None, "reason": "no_snapshot"}
                )
                continue
            items.append({"ticker": ticker, "snapshot": _snapshot_payload(snap)})
        return Response(
            {
                "as_of": as_of.isoformat(),
                "model_type": model_type,
                "items": items,
            }
        )


class RegimeHistoryView(APIView):
    """Historical snapshots in a date range. Drives the sparkline widget."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, ticker: str) -> Response:
        model_type = _parse_model_type(request)
        try:
            frm = dt.date.fromisoformat(request.query_params.get("from") or "")
        except ValueError:
            frm = dt.date.today() - dt.timedelta(days=60)
        try:
            to = dt.date.fromisoformat(request.query_params.get("to") or "")
        except ValueError:
            to = dt.date.today()
        rows = (
            RegimeSnapshot.objects.filter(
                ticker=ticker.upper(),
                model_type=model_type,
                as_of_date__gte=frm,
                as_of_date__lte=to,
            )
            .order_by("as_of_date")
            .values(
                "as_of_date", "current_state",
                "bull_prob_1d", "sideways_prob_1d", "bear_prob_1d",
                "bull_minus_bear_1d",
                "current_state_persistence",
            )
        )
        items = [
            {
                "as_of_date": r["as_of_date"].isoformat(),
                "current_state": r["current_state"],
                "bull_prob_1d": r["bull_prob_1d"],
                "sideways_prob_1d": r["sideways_prob_1d"],
                "bear_prob_1d": r["bear_prob_1d"],
                "bull_minus_bear_1d": r["bull_minus_bear_1d"],
                "current_state_persistence": r["current_state_persistence"],
            }
            for r in rows
        ]
        return Response(
            {
                "ticker": ticker.upper(),
                "model_type": model_type,
                "from": frm.isoformat(),
                "to": to.isoformat(),
                "items": items,
            }
        )


class TickerSparklineView(APIView):
    """Recent close-price series for a ticker — drives FE sparklines."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, ticker: str) -> Response:
        as_of = _parse_as_of(request)
        try:
            days = int(request.query_params.get("days", 60))
        except ValueError:
            days = 60
        days = max(5, min(365, days))
        start = as_of - dt.timedelta(days=days)
        rows = (
            DailyBar.objects.filter(
                ticker=ticker.upper(),
                date__gte=start,
                date__lte=as_of,
            )
            .order_by("date")
            .values_list("date", "close")
        )
        bars = [{"date": d.isoformat(), "close": float(c)} for d, c in rows]
        return Response({"ticker": ticker.upper(), "as_of": as_of.isoformat(), "bars": bars})


def _dec_to_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class TickerProfileView(APIView):
    """WS-2: single-ticker profile — name + latest market cap / P/E / EPS.

    Strict "today" data: the response is labelled `as_of` (today's date or
    the quote timestamp). Must never be imported by a backtest/PIT path.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, ticker: str) -> Response:
        from .providers.factory import get_fmp_provider

        sym = ticker.upper()
        cache_key = f"profile:fmp:{sym}"
        cached = cache_get(cache_key)
        if cached:
            return Response(cached)

        try:
            fmp = get_fmp_provider(user=request.user)
        except RuntimeError as exc:
            # P2n contract: surface the actionable "Set your FMP key" message.
            cp = CompanyProfile.objects.filter(ticker=sym).first()
            return Response(
                {
                    "ticker": sym,
                    "name": cp.name if cp else "",
                    "exchange": cp.exchange if cp else "",
                    "sector": cp.sector if cp else "",
                    "price": None,
                    "market_cap": None,
                    "pe_ratio": None,
                    "eps": None,
                    "as_of": dt.date.today().isoformat(),
                    "detail": str(exc),
                },
                status=200,
            )

        try:
            snap = fmp.get_quote_profile(sym)
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.warning("ticker profile fmp failure ticker=%s err=%s", sym, exc)
            snap = None

        if snap is None:
            return Response(
                {
                    "ticker": sym,
                    "name": "",
                    "exchange": "",
                    "sector": "",
                    "price": None,
                    "market_cap": None,
                    "pe_ratio": None,
                    "eps": None,
                    "as_of": dt.date.today().isoformat(),
                    "detail": "no profile available",
                },
                status=200,
            )

        # Upsert slow-changing identity into shared reference table.
        if snap.name or snap.exchange or snap.sector:
            CompanyProfile.objects.update_or_create(
                ticker=sym,
                defaults={
                    "name": snap.name,
                    "exchange": snap.exchange,
                    "sector": snap.sector,
                },
            )

        payload = {
            "ticker": sym,
            "name": snap.name,
            "exchange": snap.exchange,
            "sector": snap.sector,
            "price": _dec_to_str(snap.price),
            "market_cap": _dec_to_str(snap.market_cap),
            "pe_ratio": _dec_to_str(snap.pe_ratio),
            "eps": _dec_to_str(snap.eps),
            "shares_outstanding": snap.shares_outstanding,
            "as_of": snap.as_of.isoformat(),
        }
        cache_set(cache_key, payload, ttl_seconds=_PROFILE_TTL_SECONDS)
        return Response(payload)


class TickerProfileBatchView(APIView):
    """WS-2: batch identity-only lookup — drives table Name columns.

    Returns `{ticker: {name, exchange, sector}}` for the requested symbols.
    Serves from `CompanyProfile` immediately for known tickers; lazily
    resolves unknown ones (bounded fan-out). Unresolved tickers return an
    empty entry so the UI can fall back to the bare symbol.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        raw = request.query_params.get("symbols") or ""
        symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
        # De-dupe while preserving order.
        seen: set[str] = set()
        symbols = [s for s in symbols if not (s in seen or seen.add(s))]
        if len(symbols) > _BATCH_MAX_SYMBOLS:
            symbols = symbols[:_BATCH_MAX_SYMBOLS]

        out: dict[str, dict[str, str]] = {}
        existing = {
            cp.ticker: cp
            for cp in CompanyProfile.objects.filter(ticker__in=symbols)
        }
        unknowns = [s for s in symbols if s not in existing]

        if unknowns:
            from .providers.factory import get_fmp_provider

            try:
                fmp = get_fmp_provider(user=request.user)
            except RuntimeError:
                fmp = None
            if fmp is not None:
                for sym in unknowns:
                    try:
                        snap = fmp.get_quote_profile(sym)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("batch profile failure ticker=%s err=%s", sym, exc)
                        snap = None
                    if snap is None:
                        continue
                    cp, _ = CompanyProfile.objects.update_or_create(
                        ticker=sym,
                        defaults={
                            "name": snap.name,
                            "exchange": snap.exchange,
                            "sector": snap.sector,
                        },
                    )
                    existing[sym] = cp

        for sym in symbols:
            cp = existing.get(sym)
            out[sym] = {
                "name": cp.name if cp else "",
                "exchange": cp.exchange if cp else "",
                "sector": cp.sector if cp else "",
            }

        return Response({"profiles": out})


class TickerNewsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, ticker: str) -> Response:
        as_of = _parse_as_of(request)
        start = as_of - dt.timedelta(days=30)
        rows = (
            NewsItem.objects.filter(
                ticker=ticker.upper(),
                published_at__date__gte=start,
                published_at__date__lte=as_of,
            )
            .order_by("-published_at")[:200]
        )
        return Response(
            {
                "ticker": ticker.upper(),
                "as_of": as_of.isoformat(),
                "items": [
                    {
                        "published_at": r.published_at.isoformat(),
                        "headline": r.headline,
                        "source": r.source,
                        "provider": r.provider,
                        "url": r.url,
                        "summary": r.summary,
                        "materiality_score": r.materiality_score,
                        "materiality_tag": r.materiality_tag,
                    }
                    for r in rows
                ],
            }
        )


# ---------------------------------------------------------------------------
# Market-news feed (P3-prereq-4) — separate from the ticker news digest above.
# ---------------------------------------------------------------------------


def _get_or_create_news_prefs(user) -> UserNewsPreferences:
    prefs, _ = UserNewsPreferences.objects.get_or_create(user=user)
    return prefs


def _serialize_market_news_item(item: MarketNewsItem, cluster_size: int) -> dict:
    return {
        "id": item.id,
        "provider": item.provider,
        "headline": item.headline,
        "summary": item.summary,
        "url": item.url,
        "image_url": item.image_url,
        "source": item.source,
        "published_at": item.published_at.isoformat(),
        "symbols": item.symbols,
        "tags": item.tags,
        "sentiment": item.sentiment or None,
        "sentiment_score": item.sentiment_score,
        "sentiment_rationale": item.sentiment_rationale or None,
        "sentiment_model": item.sentiment_model or None,
        "cluster_size": cluster_size,
    }


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


NEWS_PAGE_SIZE = 12
NEWS_MAX_TOTAL = 48


class MarketNewsFeedView(APIView):
    """``GET /api/news/feed/`` — paginated ranked market-news feed.

    Query params:
      - ``page=N`` (1-indexed, default 1) — which page of ``NEWS_PAGE_SIZE``
        items to return. Capped at ``ceil(NEWS_MAX_TOTAL / NEWS_PAGE_SIZE)``.
      - ``refresh=1`` force a provider fetch (otherwise the 15-min freshness
        short-circuit keeps providers happy). Rate-limited 1 req / 60 s / user.
        Only valid on page 1 — pagination never re-fetches.

    Behaviour: fetch → rank → cap at ``NEWS_MAX_TOTAL`` → slice the page →
    classify sentiment on the returned slice (one batched LLM call, only on
    rows not yet scored by the active model, only when sentiment is enabled)
    → assemble.

    Never 500s on a provider/LLM failure — failures degrade into ``warnings``
    or ``sentiment_warning`` and the page renders from whatever remains.
    """

    permission_classes = [permissions.IsAuthenticated]

    # Class-level in-process rate limiter for ``?refresh=1`` (1 / 60s / user).
    _last_refresh_at: dict[int, float] = {}  # noqa: RUF012

    def get(self, request: Request) -> Response:
        from .market_news_rank import rank_feed
        from .market_news_sentiment import classify, is_allowed_sentiment_model
        from .providers.factory import get_market_news_service

        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        max_page = (NEWS_MAX_TOTAL + NEWS_PAGE_SIZE - 1) // NEWS_PAGE_SIZE
        page = min(page, max_page)

        force = request.query_params.get("refresh") in ("1", "true", "yes")
        if force:
            now = time.monotonic()
            last = self._last_refresh_at.get(request.user.id, 0.0)
            if now - last < 60.0:
                wait = int(60 - (now - last))
                return Response(
                    {"detail": f"Refreshing too fast — try again in {wait}s."},
                    status=429,
                )
            self._last_refresh_at[request.user.id] = now

        prefs = _get_or_create_news_prefs(request.user)

        # Only the first page is allowed to bypass the freshness short-circuit
        # via ?refresh=1; deeper pages always serve from the persisted set.
        do_force = force and page == 1
        service = get_market_news_service(user=request.user)
        try:
            rows = service.fetch_latest(force=do_force)
        except Exception as exc:  # noqa: BLE001 - never let the page 500.
            log.warning("market_news fetch_latest error err=%s", exc)
            rows = list(MarketNewsItem.objects.all()[:200])

        clusters = rank_feed(rows, now=timezone.now())
        ranked_total = min(len(clusters), NEWS_MAX_TOTAL)
        start = (page - 1) * NEWS_PAGE_SIZE
        end = min(start + NEWS_PAGE_SIZE, ranked_total)
        page_clusters = clusters[start:end]
        warnings = list(service.warnings)
        providers_used = service.providers_used
        needs_keys = (not providers_used) and not rows

        sentiment_warning: str | None = None
        if (
            prefs.sentiment_enabled
            and page_clusters
            and is_allowed_sentiment_model(prefs.sentiment_model)
        ):
            targets = [c.representative for c in page_clusters]
            ok, warn = classify(
                targets,
                model_id=prefs.sentiment_model,
                user_id=request.user.id,
            )
            if not ok:
                sentiment_warning = warn

        items = [
            _serialize_market_news_item(c.representative, c.cluster_size)
            for c in page_clusters
        ]
        # ``generated_at`` = most recent fetched_at among returned rows.
        generated_at = (
            max(
                (c.representative.fetched_at for c in page_clusters),
                default=timezone.now(),
            )
        ).isoformat()

        payload = {
            "items": items,
            "page": page,
            "page_size": NEWS_PAGE_SIZE,
            "total_available": ranked_total,
            "has_more": end < ranked_total,
            "generated_at": generated_at,
            "sentiment_enabled": prefs.sentiment_enabled,
            "sentiment_model": prefs.sentiment_model if prefs.sentiment_enabled else None,
            "providers_used": providers_used,
            "warnings": warnings,
            "needs_keys": needs_keys,
            "chyron_enabled": prefs.chyron_enabled,
            "chyron_item_count": _clamp(prefs.chyron_item_count, 5, 10),
            "ranking_basis": (
                "Ranked by recency, breadth of coverage & source weight."
            ),
        }
        if sentiment_warning:
            payload["sentiment_warning"] = sentiment_warning
        return Response(payload)


def _serialize_prefs(prefs: UserNewsPreferences) -> dict:
    return {
        "sentiment_enabled": prefs.sentiment_enabled,
        "sentiment_model": prefs.sentiment_model,
        "chyron_enabled": prefs.chyron_enabled,
        "chyron_item_count": prefs.chyron_item_count,
        "feed_item_count": prefs.feed_item_count,
    }


def _serialize_sentiment_choices() -> list[dict]:
    from .market_news_sentiment import frugal_sentiment_models

    return [
        {
            "id": m.id,
            "display_name": m.display_name,
            "price_in_per_mtok": (
                float(m.price_in_per_mtok) if m.price_in_per_mtok is not None else None
            ),
            "price_out_per_mtok": (
                float(m.price_out_per_mtok)
                if m.price_out_per_mtok is not None
                else None
            ),
        }
        for m in frugal_sentiment_models()
    ]


class NewsPreferencesView(APIView):
    """``GET|PUT /api/news/preferences/`` — per-user News settings."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        prefs = _get_or_create_news_prefs(request.user)
        return Response(
            {
                "preferences": _serialize_prefs(prefs),
                "sentiment_model_choices": _serialize_sentiment_choices(),
            }
        )

    def put(self, request: Request) -> Response:
        from .market_news_sentiment import is_allowed_sentiment_model

        prefs = _get_or_create_news_prefs(request.user)
        body = request.data or {}
        if not isinstance(body, dict):
            return Response({"detail": "Body must be an object."}, status=400)

        if "sentiment_enabled" in body:
            prefs.sentiment_enabled = bool(body["sentiment_enabled"])
        if "chyron_enabled" in body:
            prefs.chyron_enabled = bool(body["chyron_enabled"])
        if "chyron_item_count" in body:
            try:
                prefs.chyron_item_count = _clamp(int(body["chyron_item_count"]), 5, 10)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "chyron_item_count must be an integer in [5, 10]."},
                    status=400,
                )
        if "feed_item_count" in body:
            # Legacy field kept on the model for backward-compat; the feed is
            # now paginated (12/page, max 48) so this preference is unused.
            try:
                prefs.feed_item_count = _clamp(int(body["feed_item_count"]), 12, 48)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "feed_item_count must be an integer in [12, 48]."},
                    status=400,
                )
        if "sentiment_model" in body:
            model_id = str(body["sentiment_model"]).strip()
            if model_id and not is_allowed_sentiment_model(model_id):
                return Response(
                    {
                        "detail": (
                            "Sentiment model is restricted to Llama and Qwen "
                            "models. Pick one from sentiment_model_choices."
                        )
                    },
                    status=400,
                )
            if model_id:
                prefs.sentiment_model = model_id

        prefs.save()
        return Response(
            {
                "preferences": _serialize_prefs(prefs),
                "sentiment_model_choices": _serialize_sentiment_choices(),
            }
        )
