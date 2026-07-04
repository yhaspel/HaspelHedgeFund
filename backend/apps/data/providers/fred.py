"""FRED / ALFRED adapter for macro time series with vintage handling.

Backtest correctness: we ALWAYS query ALFRED's vintages endpoint and pick
the latest `vintage_date <= as_of` for each observation. The standard FRED
endpoint returns the LATEST revised value, which leaks future revisions
into past dates — forbidden here.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import httpx
from django.conf import settings
from django.db import transaction

from ..models import MacroSeries
from ._http import make_client

ALFRED_BASE = "https://api.stlouisfed.org/fred"


@dataclass(frozen=True)
class MacroObservation:
    series_id: str
    date: dt.date
    vintage_date: dt.date
    value: Decimal | None


class FredProvider:
    name = "fred"

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.FRED_API_KEY
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY is not set")
        self._http = http or make_client(timeout=30.0)

    def get_latest_value(
        self, series_id: str, *, as_of: dt.date
    ) -> MacroObservation | None:
        """Latest observation as it was known on `as_of`.

        Reads cache first; otherwise fetches the full vintage series and
        backfills MacroSeries rows for everything <= as_of.
        """
        cached = (
            MacroSeries.objects.filter(
                series_id=series_id, vintage_date__lte=as_of
            )
            .order_by("-date", "-vintage_date")
            .first()
        )
        if cached and cached.vintage_date >= as_of - dt.timedelta(days=7):
            return _to_obs(cached)

        rows = self._fetch_vintage_observations(series_id, as_of=as_of)
        with transaction.atomic():
            MacroSeries.objects.bulk_create(rows, ignore_conflicts=True)

        latest = (
            MacroSeries.objects.filter(
                series_id=series_id, vintage_date__lte=as_of
            )
            .order_by("-date", "-vintage_date")
            .first()
        )
        return _to_obs(latest) if latest else None

    def _fetch_vintage_observations(
        self, series_id: str, *, as_of: dt.date
    ) -> list[MacroSeries]:
        """ALFRED `series/observations` with realtime_start=realtime_end=as_of
        returns the values for that series as published on `as_of`.
        """
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "realtime_start": as_of.isoformat(),
            "realtime_end": as_of.isoformat(),
            "observation_end": as_of.isoformat(),
        }
        resp = self._http.get(f"{ALFRED_BASE}/series/observations", params=params)
        resp.raise_for_status()
        out: list[MacroSeries] = []
        for o in resp.json().get("observations", []):
            v = o.get("value")
            try:
                val = Decimal(v) if v not in (".", None, "") else None
            except Exception:
                val = None
            out.append(
                MacroSeries(
                    series_id=series_id,
                    date=dt.date.fromisoformat(o["date"]),
                    vintage_date=dt.date.fromisoformat(o.get("realtime_start", as_of.isoformat())),
                    value=val,
                )
            )
        return out


def _to_obs(r: MacroSeries) -> MacroObservation:
    return MacroObservation(
        series_id=r.series_id, date=r.date, vintage_date=r.vintage_date, value=r.value
    )
