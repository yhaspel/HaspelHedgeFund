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

from ..interfaces import (
    Bar,
    FilerHolding,
    FilerPortfolio,
    FundamentalRow,
    IssuerOwnershipSummary,
    ProfileSnapshot,
    QuoteSnapshot,
    ScreenerRow,
)
from ..models import DailyBar, Fundamental
from .errors import OwnershipNotEntitled

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

    # ---- 13F ownership (P4; Ultimate-gated, entitlement-probed) ----------

    def _ownership_get(self, path, params=None):
        """GET an FMP ownership endpoint; raise OwnershipNotEntitled on paywall."""
        params = dict(params or {})
        params["apikey"] = self.api_key
        resp = self._http.get(f"{BASE_URL}/{path}", params=params)
        if resp.status_code in (401, 402, 403):
            raise OwnershipNotEntitled(
                "Your FMP plan does not include 13F / institutional ownership "
                "(Ultimate tier required). Falling back to SEC EDGAR."
            )
        resp.raise_for_status()
        return resp.json() or []

    def get_issuer_ownership(self, ticker, *, as_of):
        """By-issuer institutional ownership (Ultimate, behind the probe).

        The exact slug lives in the institutional-ownership/* family; a wrong
        guess raises/falls through to EDGAR, so the resolver stays correct.
        """
        from ..models import IssuerOwnershipSnapshot

        data = self._ownership_get(
            "institutional-ownership/symbol-positions-summary",
            {"symbol": ticker},
        )
        rows = data if isinstance(data, list) else [data]
        if not rows:
            return None
        # PIT gate: FMP returns periods relative to *today*, so keep only those
        # whose filing date is knowable as of `as_of`, then take the latest.
        # This is what stops look-ahead on an entitled (Ultimate) key.
        candidates: list[tuple[dt.date, dt.date, dict]] = []
        for r in rows:
            period = r.get("date") or r.get("period")
            try:
                pe = dt.date.fromisoformat(str(period)[:10])
            except (TypeError, ValueError):
                continue
            filed = _fmp_filing_date(r, pe)
            if filed <= as_of:
                candidates.append((pe, filed, r))
        if not candidates:
            return None
        period_end, filed_at, row = max(candidates, key=lambda c: (c[0], c[1]))
        num_holders = int(row.get("investorsHolding", 0) or 0)
        total_value = int(float(row.get("totalInvested", 0) or 0))
        total_shares = int(float(row.get("numberOf13Fshares", 0) or 0))
        # ownership_pct (% of shares outstanding) is FMP-only per the plan;
        # it now rides the same filing-date gate above, so it is reported only
        # for a period that was knowable as of `as_of` (never a today-relative
        # figure leaking into a backtest).
        own_pct = row.get("ownershipPercent")
        ownership_pct = float(own_pct) if own_pct is not None else None
        qoq = row.get("totalInvestedChange")
        qoq_pct = None
        if qoq is not None and total_value:
            try:
                prior = total_value - float(qoq)
                if prior:
                    qoq_pct = float(qoq) / prior * 100
            except (TypeError, ValueError):
                qoq_pct = None
        IssuerOwnershipSnapshot.objects.update_or_create(
            ticker=ticker.upper(),
            period_end=period_end,
            source="fmp",
            defaults={
                "as_of_date": filed_at,
                "num_holders": num_holders,
                "total_shares": total_shares,
                "total_value_usd": total_value,
                "institutional_ownership_pct": ownership_pct,
                "ownership_pct": ownership_pct,
                "qoq_value_change_pct": qoq_pct,
                "top_holders": [],
            },
        )
        return IssuerOwnershipSummary(
            ticker=ticker.upper(),
            period_end=period_end,
            as_of=filed_at,
            num_holders=num_holders,
            total_shares=total_shares,
            total_value_usd=total_value,
            institutional_ownership_pct=ownership_pct,
            ownership_pct=ownership_pct,
            qoq_value_change_pct=qoq_pct,
            top_holders=[],
            new_positions=[],
            closed_positions=[],
            source="fmp",
        )

    def get_filer_portfolio(self, filer_cik, *, as_of):
        """By-filer 13F portfolio (Ultimate, behind the probe)."""
        from ..models import InstitutionalHolding

        data = self._ownership_get(
            "institutional-ownership/portfolio-holdings",
            {"cik": filer_cik},
        )
        rows = data if isinstance(data, list) else [data]
        if not rows:
            return None
        # PIT gate: group rows by report period, keep only periods whose filing
        # date is knowable as of `as_of`, and take the latest qualifying one.
        # Persist filed_at = the real filing date, never the caller's as_of.
        by_period: dict[dt.date, list[dict]] = {}
        for r in rows:
            try:
                pe = dt.date.fromisoformat(str(r.get("date"))[:10])
            except (TypeError, ValueError):
                continue
            by_period.setdefault(pe, []).append(r)
        qualifying = []
        for pe, prows in by_period.items():
            filed = _fmp_filing_date(prows[0], pe)
            if filed <= as_of:
                qualifying.append((pe, filed, prows))
        if not qualifying:
            return None
        period_end, filed_at, prows = max(qualifying, key=lambda c: (c[0], c[1]))
        filer_name = prows[0].get("investorName", "")
        objs = []
        total_value = 0
        for r in prows:
            value_usd = int(float(r.get("marketValue", 0) or 0))
            total_value += value_usd
            objs.append(
                InstitutionalHolding(
                    filer_cik=str(filer_cik),
                    filer_name=filer_name,
                    issuer_cusip=r.get("cusip", ""),
                    issuer_name=r.get("securityName", ""),
                    ticker=(r.get("symbol", "") or "").upper(),
                    period_end=period_end,
                    filed_at=filed_at,
                    shares=int(float(r.get("sharesNumber", 0) or 0)),
                    value_usd=value_usd,
                    put_call="",
                    source="fmp",
                )
            )
        if objs:
            InstitutionalHolding.objects.bulk_create(objs, ignore_conflicts=True)
        holdings = [
            FilerHolding(
                issuer_cusip=o.issuer_cusip,
                issuer_name=o.issuer_name,
                ticker=o.ticker,
                shares=int(o.shares),
                value_usd=int(o.value_usd),
                put_call=o.put_call,
                weight_pct=(
                    round(int(o.value_usd) / total_value * 100, 4)
                    if total_value
                    else None
                ),
            )
            for o in objs
        ]
        return FilerPortfolio(
            filer_cik=str(filer_cik),
            filer_name=filer_name,
            period_end=period_end,
            as_of=filed_at,
            total_value_usd=total_value,
            holdings=holdings,
            source="fmp",
        )


def _fmp_filing_date(row: dict, period_end: dt.date) -> dt.date:
    """Best-effort real SEC filing/accepted date for one FMP 13F row.

    The point-in-time gate needs the date the data became *knowable*, not the
    caller's query date. FMP's institutional-ownership family returns the
    report period in ``date``; the actual filing date, when present, is under
    one of the keys below. When FMP omits it we fall back to the statutory 13F
    deadline (``period_end + 45 days``) so a quarter is never admitted before
    it could have been filed — conservative, matching the EDGAR snapshot gate.
    """
    for k in ("filingDate", "acceptedDate", "reportedDate", "dateFiled"):
        v = row.get(k)
        if v:
            try:
                return dt.date.fromisoformat(str(v)[:10])
            except ValueError:
                continue
    return period_end + dt.timedelta(days=45)


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
