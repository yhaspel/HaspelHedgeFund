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

from apps.watchlists.services import watchlist_tickers

from .datasource import get_screener_datasource
from .fields import CAPABILITY_LABEL
from .models import SavedScreen
from .pipeline import (
    ScreenerValidationError,
    run_screen,
    validate_filters,
)
from .presets import PRESETS, preset_missing_capabilities
from .serializers import (
    SavedScreenSerializer,
    serialize_fields,
)


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
        # `last is None` ⇒ "this user has never run a screen in this process".
        # Bare `.get(..., 0.0)` incorrectly throttled the first call during
        # the first 5s of process uptime (see data/views.py for the news
        # variant of the same bug).
        last = self._last_run_at.get(request.user.id)
        if last is not None and now - last < 5.0:
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

        # Tickers across all of the user's named lists, for the in_watchlist
        # flag (one cheap query).
        watchlist_set: set[str] = watchlist_tickers(request.user)

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


# Watchlist endpoints moved to ``apps.watchlists`` (P3b — named lists). The
# screener now only reads the union of tickers via ``watchlist_tickers`` for
# the ``in_watchlist`` flag above.
