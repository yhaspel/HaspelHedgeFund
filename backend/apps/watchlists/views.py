"""API for named watchlists (P3b).

Routes (mounted at ``/api/watchlists/``):

* ``GET|POST  /``                        — list / create the user's lists
* ``GET|PATCH|DELETE /<ref>/``           — detail (enriched tickers) / rename / delete
* ``POST      /<ref>/tickers/``          — add a ticker
* ``DELETE    /<ref>/tickers/<ticker>/`` — remove a ticker

``<ref>`` is either a numeric list id or the literal ``default`` (resolves to
the user's default list — what the dashboard card / store target).

Enrichment reuses ``screener.pipeline.enrich_watchlist`` (today-data only); the
import direction is watchlists → screener.pipeline, never the reverse.
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from django.db.models import Count
from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.screener.datasource import get_screener_datasource
from apps.screener.pipeline import enrich_watchlist

from .models import Watchlist
from .serializers import WatchlistSerializer, WatchlistTickerSerializer
from .services import (
    MAX_WATCHLISTS_PER_USER,
    WATCHLIST_CAP,
    resolve_watchlist,
)


def _err(message: str, code: int = status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"detail": message}, status=code)


def _coerce(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return [_coerce(x) for x in v]
    if isinstance(v, dict):
        return {k: _coerce(x) for k, x in v.items()}
    if isinstance(v, Decimal):
        return str(v)
    return v


def _enriched_rows(watchlist: Watchlist, user: Any) -> list[dict[str, Any]]:
    """Return per-ticker rows for the panel: id/note/added_at + live price /
    change / rvol when an FMP datasource is available, else bare rows."""
    items = list(watchlist.tickers.order_by("-added_at"))
    if not items:
        return []
    tickers = [it.ticker for it in items]
    enriched_by: dict[str, Any] = {}
    try:
        ds = get_screener_datasource(user)
        for row in enrich_watchlist(tickers, user=user, datasource=ds):
            enriched_by[row.ticker.upper()] = row
    except RuntimeError:
        enriched_by = {}  # no FMP key — bare rows

    out: list[dict[str, Any]] = []
    for it in items:
        tk = it.ticker.upper()
        row = enriched_by.get(tk)
        if row is None:
            out.append(
                {
                    "id": it.id,
                    "ticker": tk,
                    "note": it.note,
                    "added_at": it.added_at.isoformat(),
                    "price": None,
                    "change_pct": None,
                    "rvol": None,
                    "volume": None,
                    "market_cap": None,
                }
            )
            continue
        d = {k: _coerce(v) for k, v in asdict(row).items() if v is not None}
        d["id"] = it.id
        d["ticker"] = tk
        d["note"] = it.note
        d["added_at"] = it.added_at.isoformat()
        out.append(d)
    return out


class WatchlistListCreateView(generics.ListCreateAPIView):
    serializer_class = WatchlistSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Watchlist.objects.filter(user=self.request.user)
            .annotate(ticker_count=Count("tickers"))
            .order_by("-is_default", "name")
        )

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if Watchlist.objects.filter(user=request.user).count() >= MAX_WATCHLISTS_PER_USER:
            return _err(
                f"Watchlist limit reached ({MAX_WATCHLISTS_PER_USER} lists)."
            )
        serializer = self.get_serializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]
        if Watchlist.objects.filter(user=request.user, name=name).exists():
            return _err("A watchlist with that name already exists.")
        # First list a user creates becomes their default.
        is_default = not Watchlist.objects.filter(user=request.user).exists()
        wl = Watchlist.objects.create(
            user=request.user, name=name, is_default=is_default
        )
        wl.ticker_count = 0
        return Response(
            WatchlistSerializer(wl).data, status=status.HTTP_201_CREATED
        )


class WatchlistDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _resolve(self, request: Request, ref: str) -> Watchlist:
        return resolve_watchlist(request.user, ref)

    def get(self, request: Request, ref: str) -> Response:
        try:
            wl = self._resolve(request, ref)
        except Watchlist.DoesNotExist:
            return _err("watchlist not found", code=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "id": wl.id,
                "name": wl.name,
                "is_default": wl.is_default,
                "items": _enriched_rows(wl, request.user),
            }
        )

    def patch(self, request: Request, ref: str) -> Response:
        try:
            wl = self._resolve(request, ref)
        except Watchlist.DoesNotExist:
            return _err("watchlist not found", code=status.HTTP_404_NOT_FOUND)
        new_name = (request.data or {}).get("name")
        if new_name is not None:
            name = str(new_name).strip()
            if not name:
                return _err("name is required")
            if len(name) > 120:
                return _err("name is too long")
            clash = (
                Watchlist.objects.filter(user=request.user, name=name)
                .exclude(pk=wl.pk)
                .exists()
            )
            if clash:
                return _err("A watchlist with that name already exists.")
            wl.name = name
            wl.save(update_fields=["name", "updated_at"])
        wl.ticker_count = wl.tickers.count()
        return Response(WatchlistSerializer(wl).data)

    def delete(self, request: Request, ref: str) -> Response:
        try:
            wl = self._resolve(request, ref)
        except Watchlist.DoesNotExist:
            return _err("watchlist not found", code=status.HTTP_404_NOT_FOUND)
        was_default = wl.is_default
        wl.delete()
        # If we removed the default and other lists remain, promote the oldest
        # so the user always has a default for the single-list consumers.
        if was_default:
            nxt = (
                Watchlist.objects.filter(user=request.user)
                .order_by("created_at")
                .first()
            )
            if nxt is not None and not nxt.is_default:
                nxt.is_default = True
                nxt.save(update_fields=["is_default"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WatchlistTickerAddView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, ref: str) -> Response:
        try:
            wl = resolve_watchlist(request.user, ref)
        except Watchlist.DoesNotExist:
            return _err("watchlist not found", code=status.HTTP_404_NOT_FOUND)
        if wl.tickers.count() >= WATCHLIST_CAP:
            return _err(
                f"Watchlist limit reached ({WATCHLIST_CAP} tickers). "
                "Remove some first."
            )
        serializer = WatchlistTickerSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        ticker = serializer.validated_data["ticker"]
        note = serializer.validated_data.get("note", "")
        obj, _ = wl.tickers.get_or_create(ticker=ticker, defaults={"note": note})
        return Response(
            WatchlistTickerSerializer(obj).data, status=status.HTTP_201_CREATED
        )


class WatchlistTickerDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request: Request, ref: str, ticker: str) -> Response:
        try:
            wl = resolve_watchlist(request.user, ref)
        except Watchlist.DoesNotExist:
            return _err("watchlist not found", code=status.HTTP_404_NOT_FOUND)
        deleted, _ = wl.tickers.filter(ticker=ticker.upper()).delete()
        if not deleted:
            return _err("ticker not on watchlist", code=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
