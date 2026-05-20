"""Read-only API: macro snapshot + ticker news digest.

Both are bare-minimum: backend computes/caches; frontend renders.
"""
from __future__ import annotations

import datetime as dt

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DailyBar, MacroSnapshot, NewsItem


def _parse_as_of(request: Request) -> dt.date:
    raw = request.query_params.get("as_of")
    if not raw:
        return dt.date.today()
    return dt.date.fromisoformat(raw)


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
