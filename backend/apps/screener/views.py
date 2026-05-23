"""API views for the market screener.

All endpoints require authentication. ``/run/`` is rate-limited at
1 call / 5 s / user (the same in-process pattern as the Manual Book's
``/api/portfolio/refresh-marks/`` view).

This module is *today*-data only and must never be imported by a
backtest or point-in-time agent path. The ``tests/test_screener_pit.py``
regression test asserts the boundary.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from rest_framework import generics, permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .datasource import get_screener_datasource
from .fields import CAPABILITY_LABEL, FIELD_REGISTRY
from .models import SavedScreen, WatchlistItem
from .pipeline import (
    ScreenerValidationError,
    enrich_watchlist,
    run_screen,
    validate_filters,
)
from .presets import PRESETS, preset_missing_capabilities
from .serializers import (
    SavedScreenSerializer,
    WatchlistItemSerializer,
    serialize_fields,
)
from .services import get_or_create_watchlist


def _err(message: str, code: int = status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"detail": message}, status=code)


def _safe_capabilities(user: Any) -> frozenset:
    """Return the active data-source capabilities, or ``frozenset()`` if no
    FMP key — so ``/fields/`` and ``/presets/`` still render."""
    try:
        return get_screener_datasource(user).capabilities()
    except RuntimeError:
        return frozenset()


class ScreenerFieldsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        caps = _safe_capabilities(request.user)
        return Response(
            {
                "fields": serialize_fields(caps),
                "capabilities": sorted(CAPABILITY_LABEL[c] for c in caps),
            }
        )


class ScreenerPresetsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        caps = _safe_capabilities(request.user)
        rows = []
        for p in PRESETS:
            missing = preset_missing_capabilities(p, caps)
            rows.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "asset_class": p.asset_class,
                    "filters": p.filters,
                    "sort": p.sort,
                    "limit": p.limit,
                    "available": not missing,
                    "requires": missing,
                }
            )
        return Response({"presets": rows})


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = asdict(row)
    for k, v in list(d.items()):
        if v is None:
            continue
        d[k] = _coerce_scalar(v)
    return d


def _coerce_scalar(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return [_coerce_scalar(x) for x in v]
    if isinstance(v, dict):
        return {k: _coerce_scalar(x) for k, x in v.items()}
    try:
        from decimal import Decimal

        if isinstance(v, Decimal):
            return str(v)
    except Exception:  # noqa: BLE001
        pass
    return v


class ScreenerRunView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    _last_run_at: dict[int, float] = {}  # noqa: RUF012 — class-level rate limiter

    def post(self, request: Request) -> Response:
        now = time.monotonic()
        last = self._last_run_at.get(request.user.id, 0.0)
        if now - last < 5.0:
            return _err(
                "Running screens too quickly — please wait a few seconds.",
                code=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            ds = get_screener_datasource(request.user)
        except RuntimeError as exc:
            return _err(str(exc), code=status.HTTP_400_BAD_REQUEST)

        body = request.data or {}
        try:
            normalized = validate_filters(
                body, available_capabilities=ds.capabilities()
            )
        except ScreenerValidationError as exc:
            return _err(str(exc), code=status.HTTP_400_BAD_REQUEST)

        # Watchlist tickers for in_watchlist flag (one cheap query).
        watchlist_set: set[str] = set()
        wl = get_or_create_watchlist(request.user)
        watchlist_set = set(
            wl.items.values_list("ticker", flat=True)
        )
        watchlist_set = {t.upper() for t in watchlist_set}

        try:
            result = run_screen(
                normalized,
                user=request.user,
                datasource=ds,
                watchlist_tickers=watchlist_set,
            )
        except RuntimeError as exc:
            return _err(str(exc), code=status.HTTP_400_BAD_REQUEST)

        # Only set the rate-limit timestamp on a successful run so an
        # invalid request does not lock the user out for 5 seconds.
        self._last_run_at[request.user.id] = now

        payload = {
            "rows": [_row_to_dict(r) for r in result.rows],
            "universe_size": result.universe_size,
            "enriched_count": result.enriched_count,
            "returned_count": result.returned_count,
            "truncated": result.truncated,
            "as_of": result.as_of.isoformat(),
            "provider": result.provider,
            "capabilities": result.capabilities,
            "warnings": result.warnings,
            "preset_id": body.get("preset_id") or "",
        }
        return Response(payload)


class SavedScreenListCreateView(generics.ListCreateAPIView):
    serializer_class = SavedScreenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SavedScreen.objects.filter(user=self.request.user).order_by("name")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class SavedScreenDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SavedScreenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SavedScreen.objects.filter(user=self.request.user)


class WatchlistView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        wl = get_or_create_watchlist(request.user)
        items = list(wl.items.order_by("-added_at"))
        tickers = [i.ticker for i in items]

        rows: list[dict[str, Any]] = []
        if tickers:
            try:
                ds = get_screener_datasource(request.user)
                enriched = enrich_watchlist(
                    tickers, user=request.user, datasource=ds
                )
            except RuntimeError:
                # No FMP key — return bare rows without enrichment.
                enriched = []

            enriched_by = {r.ticker: r for r in enriched}
            for it in items:
                tk = it.ticker.upper()
                row = enriched_by.get(tk)
                if row is None:
                    rows.append(
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
                d = _row_to_dict(row)
                d["id"] = it.id
                d["note"] = it.note
                d["added_at"] = it.added_at.isoformat()
                rows.append(d)
        return Response({"items": rows, "name": wl.name})

    def post(self, request: Request) -> Response:
        wl = get_or_create_watchlist(request.user)
        if wl.items.count() >= 100:
            return _err(
                "Watchlist limit reached (100 tickers). Remove some first.",
            )
        serializer = WatchlistItemSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        ticker = serializer.validated_data["ticker"]
        note = serializer.validated_data.get("note", "")
        obj, _ = WatchlistItem.objects.get_or_create(
            watchlist=wl, ticker=ticker, defaults={"note": note}
        )
        return Response(
            WatchlistItemSerializer(obj).data,
            status=status.HTTP_201_CREATED,
        )


class WatchlistItemDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request: Request, ticker: str) -> Response:
        wl = get_or_create_watchlist(request.user)
        deleted, _ = wl.items.filter(ticker=ticker.upper()).delete()
        if not deleted:
            return _err("ticker not on watchlist", code=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
