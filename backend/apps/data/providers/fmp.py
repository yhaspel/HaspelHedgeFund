"""Financial Modeling Prep (Premium) adapter.

API docs: https://site.financialmodelingprep.com/developer/docs

This provider:
  - fetches OHLCV via /historical-price-full
  - fetches fundamentals via /income-statement, /balance-sheet-statement,
    /cash-flow-statement, /ratios — using `acceptedDate` (SEC accepted
    timestamp) as the point-in-time `as_of_date`.

Every call is gated by `as_of` at the SQL level: rows whose `as_of_date`
(or bar date) is strictly after `as_of` are dropped before return. That
guarantee is also tested in `tests/test_data_provider.py`.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

import httpx
from django.conf import settings
from django.db import transaction

from ..interfaces import Bar, FundamentalRow, ProfileSnapshot, QuoteSnapshot, ScreenerRow
from ..models import DailyBar, Fundamental

BASE_URL = "https://financialmodelingprep.com/stable"
SOURCE = "fmp"

log = logging.getLogger(__name__)

# Map our canonical metric names to (statement, json field).
METRIC_MAP: dict[str, tuple[str, str]] = {
    "revenue": ("income-statement", "revenue"),
    "gross_profit": ("income-statement", "grossProfit"),
    "operating_income": ("income-statement", "operatingIncome"),
    "net_income": ("income-statement", "netIncome"),
    "operating_cash_flow": ("cash-flow-statement", "operatingCashFlow"),
    "capex": ("cash-flow-statement", "capitalExpenditure"),
    "free_cash_flow": ("cash-flow-statement", "freeCashFlow"),
    "operating_cash_flow_alt": ("cash-flow-statement", "netCashProvidedByOperatingActivities"),
    "total_assets": ("balance-sheet-statement", "totalAssets"),
    "total_debt": ("balance-sheet-statement", "totalDebt"),
    "total_equity": ("balance-sheet-statement", "totalStockholdersEquity"),
}


class FmpProvider:
    name = SOURCE

    def __init__(self, api_key: str | None = None, http: httpx.Client | None = None) -> None:
        self.api_key = api_key or settings.FMP_API_KEY
        if not self.api_key:
            raise RuntimeError("FMP_API_KEY is not configured")
        self._http = http or httpx.Client(timeout=30.0)

    # ---- intraday quote ----------------------------------------------

    def get_latest_quote(self, ticker: str) -> tuple[Decimal, dt.datetime] | None:
        """P3: latest intraday quote for the Manual Book's `delayed` /
        `manual` cadence modes. Returns `(price, as_of_utc)` or `None` if
        FMP returns no payload.

        Requires the FMP premium plan. Real-time vs ~15-min delayed
        depends on the user's entitlement on the supplied key.
        """
        url = f"{BASE_URL}/quote-short"
        params = {"symbol": ticker, "apikey": self.api_key}
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload if isinstance(payload, list) else []
        if not rows:
            # /quote-short returns [] for unknown tickers; try the longer endpoint.
            url = f"{BASE_URL}/quote"
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload if isinstance(payload, list) else []
        if not rows:
            return None
        row = rows[0]
        price = row.get("price") or row.get("c")
        if price is None:
            return None
        ts = row.get("timestamp")
        as_of = (
            dt.datetime.fromtimestamp(int(ts), tz=dt.UTC)
            if ts
            else dt.datetime.now(tz=dt.UTC)
        )
        return Decimal(str(price)), as_of

    # ---- profile (name + latest quote-derived metrics) ----------------

    def get_quote_profile(self, ticker: str) -> ProfileSnapshot | None:
        """WS-2: full ticker profile — name, exchange + latest price,
        market cap, P/E and EPS.

        Name / price / market cap come from the FMP *stable* `/quote`
        endpoint. `/quote` does **not** carry P/E or EPS, so those are
        pulled from `/ratios-ttm`. Both take the ticker as a `?symbol=`
        query param — the convention every other stable call here uses.

        Returns `None` for unknown tickers / empty `/quote` payload.
        Caller is responsible for caching (TTL) and for upserting
        reference fields into `CompanyProfile`.
        """
        resp = self._http.get(
            f"{BASE_URL}/quote",
            params={"symbol": ticker, "apikey": self.api_key},
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload if isinstance(payload, list) else []
        if not rows:
            return None
        row = rows[0]
        price = row.get("price") or row.get("c")
        ts = row.get("timestamp")
        as_of = (
            dt.datetime.fromtimestamp(int(ts), tz=dt.UTC).date()
            if ts
            else dt.date.today()
        )
        # /quote carries name + price + market cap but not P/E or EPS;
        # those come from /ratios-ttm. A ratios failure degrades to
        # (None, None) so the rest of the profile still renders.
        pe_ratio, eps = self._get_ttm_ratios(ticker)
        return ProfileSnapshot(
            ticker=ticker.upper(),
            name=str(row.get("name") or ""),
            exchange=str(row.get("exchange") or ""),
            sector="",  # /quote does not return sector; left blank
            price=Decimal(str(price)) if price is not None else None,
            market_cap=_dec(row.get("marketCap")),
            pe_ratio=pe_ratio,
            eps=eps,
            shares_outstanding=None,  # not returned by /quote
            as_of=as_of,
        )

    def _get_ttm_ratios(
        self, ticker: str
    ) -> tuple[Decimal | None, Decimal | None]:
        """Trailing-twelve-month P/E and EPS from FMP `/ratios-ttm`.

        Returns ``(pe_ratio, eps)``; either element is ``None`` when the
        metric is absent. Never raises — a ratios outage must not sink the
        whole profile popover, it just drops P/E and EPS.
        """
        try:
            resp = self._http.get(
                f"{BASE_URL}/ratios-ttm",
                params={"symbol": ticker, "apikey": self.api_key},
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            log.warning("fmp ratios-ttm failure ticker=%s err=%s", ticker, exc)
            return None, None
        rows = payload if isinstance(payload, list) else []
        if not rows:
            return None, None
        row = rows[0]
        return (
            _dec(row.get("priceToEarningsRatioTTM")),
            _dec(row.get("netIncomePerShareTTM")),
        )

    # ---- screener (P3 prereq 3) --------------------------------------

    def screen_companies(self, **params: Any) -> list[ScreenerRow]:
        """P3 prereq 3 stage 1: coarse universe filter via FMP
        ``/company-screener``.

        Caller supplies FMP-native param names (``marketCapMoreThan``,
        ``priceLowerThan``, ``volumeMoreThan``, ``betaMoreThan``, ``sector``,
        ``industry``, ``exchange``, ``country``, ``isEtf``, ``isFund``,
        ``limit``, …). ``isActivelyTrading=true`` and ``isFund=false`` are
        always sent unless the caller explicitly overrides them.

        Returns ``[]`` on empty payload. Raises ``httpx.HTTPStatusError``
        on a non-2xx response so the pipeline can catch 402/403 (plan
        does not include the Stock Screener endpoint) and surface an
        actionable error message.

        *Today* data — not PIT-gated.
        """
        url = f"{BASE_URL}/company-screener"
        query: dict[str, Any] = {"apikey": self.api_key}
        # Caller-supplied params win, but the safety defaults below are set
        # only when the caller has not provided them.
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, bool):
                query[k] = "true" if v else "false"
            else:
                query[k] = v
        query.setdefault("isActivelyTrading", "true")
        query.setdefault("isFund", "false")
        resp = self._http.get(url, params=query)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload if isinstance(payload, list) else []
        out: list[ScreenerRow] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            volume_raw = row.get("volume")
            try:
                volume = int(volume_raw) if volume_raw is not None else None
            except (TypeError, ValueError):
                volume = None
            out.append(
                ScreenerRow(
                    ticker=symbol,
                    name=str(row.get("companyName") or ""),
                    market_cap=_dec(row.get("marketCap")),
                    price=_dec(row.get("price")),
                    volume=volume,
                    beta=_dec(row.get("beta")),
                    sector=str(row.get("sector") or ""),
                    industry=str(row.get("industry") or ""),
                    exchange=str(
                        row.get("exchangeShortName") or row.get("exchange") or ""
                    ),
                    country=str(row.get("country") or ""),
                    is_etf=bool(row.get("isEtf") or False),
                    is_fund=bool(row.get("isFund") or False),
                    last_annual_dividend=_dec(row.get("lastAnnualDividend")),
                )
            )
        return out

    def get_quote_batch(self, tickers: list[str]) -> dict[str, QuoteSnapshot]:
        """P3 prereq 3 stage 2: live intraday quotes for many symbols via
        FMP ``/batch-quote``.

        Chunks at ≤ 100 symbols per request. Returns a dict keyed by the
        upper-cased ticker. Tickers FMP drops from the payload are simply
        absent from the dict (the caller treats absence as "not enriched"
        rather than raising).

        Requires the FMP premium plan for live intraday — non-premium keys
        typically return an empty payload. The screener pipeline catches
        the empty result and falls back to EOD bars with a per-row warning.

        *Today* data — not PIT-gated.
        """
        out: dict[str, QuoteSnapshot] = {}
        if not tickers:
            return out
        unique = sorted({t.upper() for t in tickers if t})
        for i in range(0, len(unique), 100):
            chunk = unique[i : i + 100]
            url = f"{BASE_URL}/batch-quote"
            params = {"symbols": ",".join(chunk), "apikey": self.api_key}
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload if isinstance(payload, list) else []
            for row in rows:
                symbol = str(row.get("symbol") or "").upper()
                if not symbol:
                    continue
                volume_raw = row.get("volume")
                try:
                    volume = int(volume_raw) if volume_raw is not None else None
                except (TypeError, ValueError):
                    volume = None
                ts = row.get("timestamp")
                as_of = (
                    dt.datetime.fromtimestamp(int(ts), tz=dt.UTC).date()
                    if ts
                    else dt.date.today()
                )
                out[symbol] = QuoteSnapshot(
                    ticker=symbol,
                    price=_dec(row.get("price")),
                    open=_dec(row.get("open")),
                    previous_close=_dec(row.get("previousClose")),
                    day_high=_dec(row.get("dayHigh")),
                    day_low=_dec(row.get("dayLow")),
                    year_high=_dec(row.get("yearHigh")),
                    year_low=_dec(row.get("yearLow")),
                    price_avg_50=_dec(row.get("priceAvg50")),
                    price_avg_200=_dec(row.get("priceAvg200")),
                    volume=volume,
                    change_pct=_dec(
                        row.get("changePercentage")
                        if row.get("changePercentage") is not None
                        else row.get("changesPercentage")
                    ),
                    market_cap=_dec(row.get("marketCap")),
                    pe_ratio=_dec(row.get("pe")),
                    eps=_dec(row.get("eps")),
                    as_of=as_of,
                )
        return out

    # ---- bars ---------------------------------------------------------

    def get_daily_bars(
        self, ticker: str, start: dt.date, end: dt.date, *, as_of: dt.date
    ) -> list[Bar]:
        if end > as_of:
            end = as_of  # never look past as_of
        self._ensure_bars_cached(ticker, start, end)
        upper = min(end, as_of)
        rows = DailyBar.objects.filter(
            ticker=ticker, source=SOURCE, date__gte=start, date__lte=upper
        ).order_by("date")
        return [
            Bar(
                ticker=r.ticker,
                date=r.date,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                adjusted_close=r.adjusted_close,
                volume=r.volume,
            )
            for r in rows
        ]

    def _ensure_bars_cached(self, ticker: str, start: dt.date, end: dt.date) -> None:
        existing = DailyBar.objects.filter(
            ticker=ticker, source=SOURCE, date__gte=start, date__lte=end
        ).count()
        # Heuristic: ~252 trading days/year; if we already have most of the range, skip.
        expected_min = max(1, int((end - start).days * 0.6))
        if existing >= expected_min:
            return
        url = f"{BASE_URL}/historical-price-eod/full"
        params = {
            "symbol": ticker,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "apikey": self.api_key,
        }
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()
        historical: list[dict[str, Any]] = payload if isinstance(payload, list) else []
        objs = [
            DailyBar(
                ticker=ticker,
                date=dt.date.fromisoformat(row["date"]),
                open=Decimal(str(row.get("open", 0))),
                high=Decimal(str(row.get("high", 0))),
                low=Decimal(str(row.get("low", 0))),
                close=Decimal(str(row.get("close", 0))),
                adjusted_close=Decimal(str(row.get("adjClose", row.get("close", 0)))),
                volume=int(row.get("volume", 0) or 0),
                source=SOURCE,
            )
            for row in historical
        ]
        with transaction.atomic():
            DailyBar.objects.bulk_create(objs, ignore_conflicts=True)

    # ---- fundamentals ------------------------------------------------

    def get_fundamentals(
        self,
        ticker: str,
        metrics: list[str],
        *,
        as_of: dt.date,
        lookback_quarters: int = 8,
    ) -> list[FundamentalRow]:
        statements = {METRIC_MAP[m][0] for m in metrics if m in METRIC_MAP}
        # FMP's `limit` returns the latest N quarters relative to *today*, not
        # to `as_of`. If as_of is far in the past we need extra depth so that
        # at least `lookback_quarters` rows with as_of_date <= as_of survive
        # the point-in-time filter. Each quarter spans ~0.25 years; the
        # `*2` slack absorbs filing-date jitter and missed quarters.
        today = dt.date.today()
        quarters_since_asof = max(0, (today - as_of).days // 90)
        # Original (P1) behavior: lookback_quarters * 2. We only bump beyond
        # that when as_of is far enough in the past that the latest-N rows
        # FMP returns wouldn't span back to it. This keeps near-current
        # backtests cassette-compatible while letting deep-history runs
        # actually get the rows they need.
        extra_quarters = max(0, quarters_since_asof - lookback_quarters)
        fetch_limit = (lookback_quarters + extra_quarters) * 2
        for stmt in statements:
            self._ensure_statement_cached(ticker, stmt, fetch_limit)
        qs = Fundamental.objects.filter(
            ticker=ticker,
            source=SOURCE,
            metric__in=metrics,
            as_of_date__lte=as_of,
        ).order_by("-period_end")
        seen: set[tuple[str, dt.date]] = set()
        out: list[FundamentalRow] = []
        for row in qs:
            key = (row.metric, row.period_end)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                FundamentalRow(
                    ticker=row.ticker,
                    as_of_date=row.as_of_date,
                    period_end=row.period_end,
                    metric=row.metric,
                    value=row.value,
                )
            )
        # Keep only most recent N periods per metric.
        by_metric: dict[str, list[FundamentalRow]] = {}
        for r in out:
            by_metric.setdefault(r.metric, []).append(r)
        result: list[FundamentalRow] = []
        for rows in by_metric.values():
            result.extend(rows[:lookback_quarters])
        return result

    def _ensure_statement_cached(self, ticker: str, statement: str, limit: int) -> None:
        url = f"{BASE_URL}/{statement}"
        params = {
            "symbol": ticker,
            "period": "quarter",
            "limit": str(limit),
            "apikey": self.api_key,
        }
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        rows: list[dict[str, Any]] = resp.json() or []
        to_create: list[Fundamental] = []
        for row in rows:
            period_end = dt.date.fromisoformat(row["date"])
            accepted = row.get("acceptedDate") or row.get("fillingDate") or row["date"]
            as_of_date = _parse_date(accepted)
            for metric, (stmt, field) in METRIC_MAP.items():
                if stmt != statement or field not in row or row[field] is None:
                    continue
                to_create.append(
                    Fundamental(
                        ticker=ticker,
                        as_of_date=as_of_date,
                        period_end=period_end,
                        metric=metric,
                        value=Decimal(str(row[field])),
                        source=SOURCE,
                    )
                )
        with transaction.atomic():
            Fundamental.objects.bulk_create(to_create, ignore_conflicts=True)


def _parse_date(value: str) -> dt.date:
    # FMP returns "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS".
    return dt.date.fromisoformat(value.split(" ")[0])


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
