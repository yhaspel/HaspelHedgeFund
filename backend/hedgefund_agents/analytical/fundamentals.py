"""Fundamentals agent. Loads quarterly statements (point-in-time correct)
and asks the LLM to summarize quality into a structured output.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.conf import settings

from .._persist import record_llm_call
from ..base import AgentState, pick_model
from ..llm.client import Message
from ..llm.structured import call_structured
from ..outputs import FundamentalsOutput
from ..registry import DEFAULT_MODELS, get_llm
from ..versioning import AgentSpec, register

METRICS = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "free_cash_flow",
    "total_debt",
    "total_equity",
    "total_assets",
]

SPEC = AgentSpec(
    agent_name="fundamentals",
    version="v1",
    default_model="openrouter:qwen/qwen3.6-27b",
    prompt="(fundamentals quality summarizer — see analytical/fundamentals.py)",
    config={"kind": "analytical"},
)
register(SPEC)


def _build_table(rows: list) -> str:
    by_metric: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for r in rows:
        by_metric[r.metric].append((r.period_end.isoformat(), r.value))
    lines = []
    for metric, series in by_metric.items():
        series.sort(reverse=True)
        formatted = ", ".join(f"{d}={v:.2f}" for d, v in series[:8])
        lines.append(f"- {metric}: {formatted}")
    return "\n".join(lines)


def _revenue_cagr(rows: list) -> float | None:
    """Annualized revenue CAGR over the trailing revenue series.

    Same formula as ``valuation._cagr`` (periods_per_year=4): a deterministic
    computation used to OVERRIDE the LLM's estimate of ``revenue_cagr_3y`` — the
    figure is arithmetic, not judgment, so the model must not be able to
    hallucinate it (P5-SH WS1.4). Returns None when the series has fewer than two
    points or any non-positive value, in which case the model's number stands.
    """
    revs = sorted(
        (r for r in rows if r.metric == "revenue"), key=lambda r: r.period_end
    )
    values = [float(r.value) for r in revs]
    if len(values) < 2 or any(v <= 0 for v in values):
        return None
    years = (len(values) - 1) / 4.0
    if years <= 0:
        return None
    return (values[-1] / values[0]) ** (1.0 / years) - 1.0


def run_fundamentals(state: AgentState) -> AgentState:
    ticker = state["ticker"]
    as_of = state["as_of_date"]
    rows = state["data_provider"].get_fundamentals(
        ticker, METRICS, as_of=as_of, lookback_quarters=8
    )
    if not rows:
        # No statements at all (ETF, ADR, unknown ticker, thin FMP tier).
        # Asking the LLM to "compute trailing ratios" off an empty table made it
        # invent a full set of plausible numbers — a quality_score and an ROIC
        # that then flowed to every persona as if measured. Technicals already
        # guards this way (<30 bars → neutral, no LLM); do the same here.
        out = FundamentalsOutput(
            revenue_cagr_3y=0.0, gross_margin=0.0, operating_margin=0.0,
            fcf_margin=0.0, roic=0.0, debt_to_equity=0.0, quality_score=0,
            available=False,
            notes=(
                f"No financial statements available for {ticker} as of "
                f"{as_of.isoformat()}; fundamentals not assessed. The numeric "
                "fields are placeholders, not measurements."
            ),
        ).model_dump()
        return {"fundamentals": out}  # type: ignore[return-value]

    table = _build_table(rows)

    # P4: best-effort, point-in-time institutional-ownership (13F) enrichment.
    ownership_block = ""
    # WAVE-3 P2: the SEC bulk 13F fallback is gone, so an empty result means
    # exactly one thing — the key is not FMP-Ultimate-entitled. Say so in the
    # output instead of leaving the ownership fields silently at their defaults.
    ownership_unavailable = False
    prov = state.get("ownership_provider")
    if prov is not None and getattr(settings, "FUNDAMENTALS_USE_13F", True):
        try:
            own = prov.get_issuer_ownership(ticker, as_of=as_of)
        except Exception:  # never break the core analysis
            own = None
        if own is not None:
            ownership_block = _format_ownership_block(own)
        else:
            ownership_unavailable = True

    provider, model = pick_model(state, "fundamentals", DEFAULT_MODELS["fundamentals"])
    client = get_llm(provider, state=state)
    system = (
        "You are a quantitative equity analyst. Given the company's last 8 "
        "quarters of fundamentals, compute trailing ratios and produce a "
        "concise quality assessment. Use ONLY the data provided."
    )
    if ownership_block:
        system += (
            " If institutional-ownership data is provided, factor the "
            "accumulation/distribution trend into quality_score and populate "
            "the ownership fields; otherwise leave them at their defaults."
        )
    user = (
        f"Ticker: {ticker}\nAs-of date: {as_of.isoformat()}\n\n"
        f"Quarterly fundamentals (most recent first):\n{table}\n\n"
        "Estimate 3-year revenue CAGR (use 12 trailing quarters worth of revenue), "
        "trailing-twelve-months gross/operating/FCF margins, approximate ROIC, "
        "debt/equity, and assign quality_score 0-100. Provide a 1-2 sentence note."
    )
    if ownership_block:
        user += f"\n\n{ownership_block}"
    from apps.backtests.cache import make_cache_ctx

    parsed, resp = call_structured(
        client,
        model=model,
        schema=FundamentalsOutput,
        messages=[Message("system", system), Message("user", user)],
        max_tokens=4096,
        cache_ctx=make_cache_ctx(state, "fundamentals"),
    )
    record_llm_call(
        run_id=state.get("run_id"),
        backtest_id=state.get("backtest_id"),
        portfolio_target_id=state.get("portfolio_target_id"),
        agent_name="fundamentals",
        resp=resp,
    )
    result = parsed.model_dump()
    if ownership_unavailable:
        from apps.data.providers.ownership import NOT_ENTITLED_REASON

        result["institutional_ownership_pct"] = None
        result["institutional_ownership_trend"] = "unknown"
        result["smart_money_note"] = NOT_ENTITLED_REASON
    # P5-SH WS1.4: replace the LLM's estimated revenue_cagr_3y with the exact
    # value computed from the revenue series. Falls back to the model's number
    # only when the series is too sparse/non-positive to compute deterministically.
    cagr = _revenue_cagr(rows)
    if cagr is not None:
        result["revenue_cagr_3y"] = cagr
    return {"fundamentals": result}  # type: ignore[return-value]


def _format_ownership_block(own) -> str:
    """Render a compact, honest 13F institutional-ownership block.

    Top-5 holders by value, holder count, total value, QoQ value change, and
    new/closed-position counts, with the inherent caveats spelled out so the
    model never overclaims (13F is long-US-equity-only and ~45 days lagged).
    """
    lines = ["Institutional ownership (SEC 13F):"]
    lines.append(f"- As-of quarter (period_end): {own.period_end}")
    lines.append(f"- Number of 13F holders: {own.num_holders}")
    if own.total_value_usd:
        lines.append(f"- Total reported value (USD): {int(own.total_value_usd):,}")
    if own.institutional_ownership_pct is not None:
        lines.append(
            f"- % of shares outstanding held by 13F filers: "
            f"{own.institutional_ownership_pct:.2f}%"
        )
    if own.qoq_value_change_pct is not None:
        lines.append(f"- QoQ change in reported value: {own.qoq_value_change_pct:+.1f}%")
    new_n = own.new_positions
    closed_n = own.closed_positions
    if isinstance(new_n, list):
        new_n = len(new_n)
    if isinstance(closed_n, list):
        closed_n = len(closed_n)
    if new_n is not None:
        lines.append(f"- Filers that opened a position this quarter: {new_n}")
    if closed_n is not None:
        lines.append(f"- Filers that closed a position this quarter: {closed_n}")
    top = list(own.top_holders or [])[:5]
    if top:
        lines.append("- Top holders by value:")
        for h in top:
            val = f"{int(h.value_usd):,}" if h.value_usd else "n/a"
            lines.append(f"    * {h.filer_name or h.filer_cik}: ${val}")
    lines.append(
        "Caveats: 13F covers only long US-listed equity positions of large "
        "managers (no shorts/cash/bonds/non-US/sub-$100M filers) and is "
        "reported with the usual ~45-day lag, so it is not current."
    )
    return "\n".join(lines)
