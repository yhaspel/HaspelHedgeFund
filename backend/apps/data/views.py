"""Read-only API: macro snapshot + Markov regime + ticker news digest.

Bare-minimum: backend computes/caches; frontend renders.
"""
from __future__ import annotations

import datetime as dt

from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DailyBar, MacroSnapshot, NewsItem, RegimeSnapshot


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
