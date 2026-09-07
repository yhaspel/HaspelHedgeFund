"""Valuation agent.

Computes three valuations in pure Python from the cached fundamentals
and last bar (no LLM in the arithmetic). The LLM only writes a 1-2
sentence summary and names the most sensitive assumption.
"""
from __future__ import annotations

import datetime as dt

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import ValuationOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AgentSpec, register

METRICS = [
    "revenue", "operating_income", "net_income",
    "free_cash_flow", "total_equity", "total_assets",
    # Needed to turn the enterprise-level estimates into PER-SHARE fair values.
    # Providers that don't map a name simply omit it (FmpProvider filters
    # unknown metrics), in which case the node reports upside_pct=None.
    "shares_outstanding", "weighted_average_shares_outstanding",
]

# Metric names, most authoritative first, that may carry a share count.
SHARE_COUNT_METRICS = ("shares_outstanding", "weighted_average_shares_outstanding")

DCF_WACC_DEFAULT = 0.09
DCF_TERMINAL_GROWTH = 0.025
DCF_HORIZON_YEARS = 10

SPEC = AgentSpec(
    agent_name="valuation",
    version="v1",
    default_model="openrouter:qwen/qwen3.6-27b",
    prompt="(valuation summarizer — see analytical/valuation.py)",
    config={
        "kind": "analytical",
        "wacc": DCF_WACC_DEFAULT,
        "terminal_growth": DCF_TERMINAL_GROWTH,
        "horizon_years": DCF_HORIZON_YEARS,
    },
)
register(SPEC)


def _series(rows, metric: str) -> list[tuple[dt.date, float]]:
    out = [(r.period_end, float(r.value)) for r in rows if r.metric == metric]
    out.sort()
    return out


def _ttm(rows, metric: str) -> float | None:
    s = _series(rows, metric)
    if not s:
        return None
    return sum(v for _, v in s[-4:])


def resolve_shares_outstanding(rows) -> float | None:
    """Latest positive share count from the fundamentals payload, or None.

    Returns None when the provider does not carry a share count for this
    ticker — the caller must then report `upside_pct=None` rather than invent
    a per-share fair value.
    """
    for metric in SHARE_COUNT_METRICS:
        series = _series(rows, metric)
        for _, value in reversed(series):
            if value and value > 0:
                return float(value)
    return None


def _cagr(values: list[float], periods_per_year: int = 4) -> float | None:
    if len(values) < 2 or values[0] <= 0 or values[-1] <= 0:
        return None
    years = (len(values) - 1) / periods_per_year
    if years <= 0:
        return None
    return (values[-1] / values[0]) ** (1 / years) - 1


def compute_dcf(
    fcf_ttm: float,
    growth: float,
    wacc: float = DCF_WACC_DEFAULT,
    terminal_g: float = DCF_TERMINAL_GROWTH,
    horizon: int = DCF_HORIZON_YEARS,
    shares_out: float = 1.0,
) -> float | None:
    if fcf_ttm is None or fcf_ttm <= 0 or wacc <= terminal_g:
        return None
    pv = 0.0
    fcf = fcf_ttm
    for t in range(1, horizon + 1):
        fcf *= 1 + growth
        pv += fcf / (1 + wacc) ** t
    terminal = fcf * (1 + terminal_g) / (wacc - terminal_g)
    pv += terminal / (1 + wacc) ** horizon
    return pv / max(shares_out, 1.0)


def compute_multiples(
    net_income_ttm: float | None, pe_peer: float = 18.0, shares_out: float = 1.0,
) -> float | None:
    if net_income_ttm is None or net_income_ttm <= 0:
        return None
    return net_income_ttm * pe_peer / max(shares_out, 1.0)


def compute_residual_income(
    equity: float | None,
    net_income_ttm: float | None,
    cost_of_equity: float = 0.10,
    shares_out: float = 1.0,
) -> float | None:
    if equity is None or net_income_ttm is None or equity <= 0:
        return None
    ri = net_income_ttm - cost_of_equity * equity
    if ri <= 0:
        return (equity) / max(shares_out, 1.0)
    return (equity + ri / cost_of_equity) / max(shares_out, 1.0)


def run_valuation(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    as_of = state["as_of_date"]
    data = state["data_provider"]
    rows = data.get_fundamentals(ticker, METRICS, as_of=as_of, lookback_quarters=12)

    revenue_series = [v for _, v in _series(rows, "revenue")]
    growth = _cagr(revenue_series) or 0.05
    fcf_ttm = _ttm(rows, "free_cash_flow")
    ni_ttm = _ttm(rows, "net_income")
    equity_latest = None
    eq_series = _series(rows, "total_equity")
    if eq_series:
        equity_latest = eq_series[-1][1]

    bars = data.get_daily_bars(ticker, as_of - dt.timedelta(days=10), as_of, as_of=as_of)
    current_price = float(bars[-1].close) if bars else 0.0

    # Per-share fair values need an authoritative share count. The old code
    # divided by 1.0 and then rescaled the whole band by
    # `current_price / mean(candidates)`, which forced mean == price — so the
    # midpoint upside was 0.0% for EVERY ticker and the band always straddled
    # the price. Now: divide by the real share count, or report nothing.
    shares_out = resolve_shares_outstanding(rows)
    note = ""
    if shares_out is None or shares_out <= 0:
        dcf_ps = mult_ps = ri_ps = None
        fv_low = fv_high = None
        upside = None
        note = (
            "Fair value not computable: no shares-outstanding figure in the "
            "fundamentals payload, so the DCF / multiples / residual-income "
            "estimates cannot be expressed per share."
        )
    else:
        dcf_ps = compute_dcf(fcf_ttm or 0.0, growth, shares_out=shares_out)
        mult_ps = compute_multiples(ni_ttm, shares_out=shares_out)
        ri_ps = compute_residual_income(equity_latest, ni_ttm, shares_out=shares_out)
        candidates = [v for v in (dcf_ps, mult_ps, ri_ps) if v is not None and v > 0]
        if not candidates:
            fv_low = fv_high = None
            upside = None
            note = (
                "Fair value not computable: none of the DCF, multiples or "
                "residual-income methods produced a positive estimate."
            )
        elif current_price <= 0:
            fv_low, fv_high = min(candidates), max(candidates)
            upside = None
            note = "Upside not computable: no price bar on or before the as-of date."
        else:
            fv_low, fv_high = min(candidates), max(candidates)
            midpoint = sum(candidates) / len(candidates)
            upside = (midpoint - current_price) / current_price * 100

    summary_input = {
        "dcf_fair_value": dcf_ps,
        "multiples_fair_value": mult_ps,
        "residual_income_fair_value": ri_ps,
        "fair_value_low": fv_low,
        "fair_value_high": fv_high,
        "current_price": current_price,
        "upside_pct": upside,
        "shares_outstanding": shares_out,
        "growth_used": growth,
        "wacc": DCF_WACC_DEFAULT,
        "terminal_growth": DCF_TERMINAL_GROWTH,
        "note": note,
    }

    default = DEFAULT_MODELS.get("valuation", ("openrouter", "qwen/qwen3.6-27b"))
    provider, model = pick_model(state, "valuation", default)
    client = get_llm(provider, state=state)
    system = (
        "You are a valuation analyst. You will be given three fair-value estimates "
        "(DCF, multiples, residual income), the current price, and the assumptions used. "
        "Return a ValuationOutput JSON object, copying the numeric fields verbatim and "
        "naming the single most sensitive assumption in <= 20 words."
    )
    user = "INPUTS:\n" + str(summary_input)
    from apps.backtests.cache import make_cache_ctx
    parsed, resp = call_structured(
        client,
        model=model,
        schema=ValuationOutput,
        messages=[Message("system", system), Message("user", user)],
        max_tokens=1024,
        cache_ctx=make_cache_ctx(state, "valuation"),
    )
    record_llm_call(
        run_id=state.get("run_id"), backtest_id=state.get("backtest_id"),
            portfolio_target_id=state.get("portfolio_target_id"),
        agent_name="valuation", resp=resp,
    )
    out = parsed.model_dump()
    # Trust our arithmetic, not the LLM's echo.
    out.update({
        "dcf_fair_value": dcf_ps,
        "multiples_fair_value": mult_ps,
        "residual_income_fair_value": ri_ps,
        "fair_value_low": fv_low,
        "fair_value_high": fv_high,
        "current_price": current_price,
        "upside_pct": upside,
        "shares_outstanding": shares_out,
        "notes": note or out.get("notes", ""),
    })
    return {"valuation": out}  # type: ignore[return-value]
