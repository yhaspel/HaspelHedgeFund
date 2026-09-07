"""Adversarial review (reviewer: runsllm) — decision / analytics-math proofs."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from hedgefund_agents.llm.client import LLMResponse, Message
from hedgefund_agents.outputs import CioOutput, ValuationOutput

# ---------------------------------------------------------------------------
# F-H: the CIO can override a Risk-Manager veto / cap (nothing re-checks it)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cio_can_flip_vetoed_hold_to_oversized_buy():
    from hedgefund_agents.portfolio.cio import run_cio

    pm_ticket = {  # what portfolio_manager.aggregate emits under a veto
        "ticker": "AAPL", "action": "hold", "target_quantity": 0.0,
        "target_weight_pct": 0.0, "aggregate_confidence": 70,
        "rationale": "Action: hold. Risk Manager veto in effect: forcing hold.",
        "dissenting_personas": [],
    }
    cio = CioOutput(ticker="AAPL", action="buy", target_weight_pct=60.0,
                    target_quantity=600.0, overrode_pm=False,   # not even self-flagged
                    outlook="looks great", confidence=90)

    class _Fake:
        provider = "fake"

        def complete(self, **kw):
            return LLMResponse(text=cio.model_dump_json(), model="m", provider="fake")

    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1),
        "decision": dict(pm_ticket),
        "risk": {"veto": True, "max_position_pct_for_this_trade": 0.0,
                 "hard_caps_applied": ["max_portfolio_drawdown_pct"], "rationale": "veto"},
    }
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_Fake()), \
         patch("hedgefund_agents.portfolio.cio.record_llm_call"):
        out = run_cio(state)
    final = out["decision"]
    # FIXED (WP-B2): the risk layer is not discretionary — the CIO ticket is
    # re-checked against the veto/cap before it becomes the decision.
    assert final["action"] == "hold"                # veto enforced
    assert final["target_weight_pct"] == 0.0
    assert final["target_quantity"] == 0.0
    assert "[RISK CLAMP]" in final["rationale"]     # and the override is visible
    assert out["cio"]["risk_clamps"]


@pytest.mark.django_db
def test_cio_weight_is_clamped_to_the_risk_cap():
    from hedgefund_agents.portfolio.cio import run_cio

    cio = CioOutput(ticker="AAPL", action="buy", target_weight_pct=60.0,
                    target_quantity=600.0, overrode_pm=False,
                    outlook="looks great", confidence=90)

    class _Fake:
        provider = "fake"

        def complete(self, **kw):
            return LLMResponse(text=cio.model_dump_json(), model="m", provider="fake")

    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1),
        "decision": {"ticker": "AAPL", "action": "buy", "target_quantity": 200.0,
                     "target_weight_pct": 20.0, "aggregate_confidence": 70,
                     "rationale": "r", "dissenting_personas": []},
        "risk": {"veto": False, "max_position_pct_for_this_trade": 0.20},
    }
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_Fake()), \
         patch("hedgefund_agents.portfolio.cio.record_llm_call"):
        out = run_cio(state)
    final = out["decision"]
    assert final["action"] == "buy"
    assert final["target_weight_pct"] == pytest.approx(20.0)   # 60% → the 20% cap
    assert final["target_quantity"] == pytest.approx(200.0)    # qty scaled with it
    assert "[RISK CLAMP]" in final["rationale"]
    assert out["cio"]["confidence"] == 90


@pytest.mark.django_db
def test_cio_budget_abort_is_not_swallowed(db):
    """The blanket `except Exception` used to turn the mid-run spend abort into
    a benign "PM ratified by fallback" and the run kept spending."""
    from apps.backtests.exceptions import BudgetExceeded
    from hedgefund_agents.portfolio.cio import run_cio

    class _Boom:
        provider = "fake"

        def complete(self, **kw):
            raise BudgetExceeded(1.0, 0.5, 1, 1)

    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1),
        "decision": {"ticker": "AAPL", "action": "buy", "target_quantity": 10.0,
                     "target_weight_pct": 8.0, "aggregate_confidence": 70,
                     "rationale": "r", "dissenting_personas": []},
    }
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_Boom()), \
         pytest.raises(BudgetExceeded):
        run_cio(state)


# ---------------------------------------------------------------------------
# F-I: a zero cap (without veto) is treated as "no cap" → 100%-of-NAV sizing
# ---------------------------------------------------------------------------

def test_zero_risk_cap_without_veto_sizes_to_max_weight():
    from hedgefund_agents.portfolio.portfolio_manager import aggregate

    personas = {n: {"signal": "bullish", "confidence": 80, "thesis": "t"}
                for n in ("buffett", "munger", "graham")}
    val = {"current_price": 100.0}
    w_cap20 = aggregate(ticker="AAPL", persona_outputs=personas, valuation=val,
                        risk={"veto": False, "max_position_pct_for_this_trade": 0.20})
    w_cap1 = aggregate(ticker="AAPL", persona_outputs=personas, valuation=val,
                       risk={"veto": False, "max_position_pct_for_this_trade": 0.01})
    w_cap0 = aggregate(ticker="AAPL", persona_outputs=personas, valuation=val,
                       risk={"veto": False, "max_position_pct_for_this_trade": 0.0})
    assert w_cap20.target_weight_pct == pytest.approx(16.0)
    assert w_cap1.target_weight_pct == pytest.approx(0.8)
    # FIXED (WP-B2): a STATED cap of 0 means "no room", not "unconstrained".
    # Tightening the cap 1% → 0% now walks the position down to nothing.
    assert w_cap0.action == "hold"
    assert w_cap0.target_weight_pct == pytest.approx(0.0)
    assert w_cap0.target_quantity == pytest.approx(0.0)
    assert "cap is 0%" in w_cap0.rationale


def test_unstated_risk_cap_is_still_treated_as_unconstrained():
    """`risk={}` / a risk manager that omits the field keeps the old sizing —
    only an EXPLICIT 0 means "no room"."""
    from hedgefund_agents.portfolio.portfolio_manager import aggregate

    personas = {n: {"signal": "bullish", "confidence": 80, "thesis": "t"}
                for n in ("buffett", "munger", "graham")}
    val = {"current_price": 100.0}
    out = aggregate(ticker="AAPL", persona_outputs=personas, valuation=val,
                    risk={"veto": False})
    assert out.action == "buy" and out.target_weight_pct > 0


# ---------------------------------------------------------------------------
# F-J: valuation upside is identically zero by construction
# ---------------------------------------------------------------------------

class _Bar:
    def __init__(self, close, adjusted_close=None):
        self.close = Decimal(str(close))
        # Readers prefer the total-return series and fall back to close.
        self.adjusted_close = None if adjusted_close is None else Decimal(str(adjusted_close))


class _Row:
    def __init__(self, metric, value, period_end):
        self.metric, self.value, self.period_end = metric, Decimal(str(value)), period_end


class _Provider:
    def __init__(self, fcf_q: float, ni_q: float, equity: float, price: float,
                 shares: float | None = 100.0):
        self.fcf_q, self.ni_q, self.equity, self.price = fcf_q, ni_q, equity, price
        self.shares = shares

    def get_daily_bars(self, ticker, start, end, *, as_of):
        return [_Bar(self.price)]

    def get_fundamentals(self, ticker, metrics, *, as_of, lookback_quarters=8):
        rows = []
        for q in range(12):
            p = dt.date(2026, 3, 31) - dt.timedelta(days=91 * q)
            rows += [
                _Row("revenue", 1_000 * (1 + 0.03 * (12 - q)), p),
                _Row("free_cash_flow", self.fcf_q, p),
                _Row("net_income", self.ni_q, p),
                _Row("total_equity", self.equity, p),
                _Row("operating_income", self.ni_q, p),
                _Row("total_assets", self.equity * 2, p),
            ]
            if self.shares is not None:
                rows.append(_Row("shares_outstanding", self.shares, p))
        return rows


class _EchoLLM:
    provider = "fake"

    def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
        return LLMResponse(
            text=ValuationOutput(fair_value_low=0, fair_value_high=0, current_price=0,
                                 upside_pct=0, most_sensitive_assumption="x").model_dump_json(),
            model=model, provider="fake",
        )


def _valuation(fcf_q, ni_q, equity, price, shares=100.0):
    from hedgefund_agents.analytical.valuation import run_valuation

    state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1),
             "data_provider": _Provider(fcf_q, ni_q, equity, price, shares)}
    with patch("hedgefund_agents.analytical.valuation.get_llm", return_value=_EchoLLM()), \
         patch("hedgefund_agents.analytical.valuation.record_llm_call"):
        return run_valuation(state)["valuation"]


@pytest.mark.django_db
@pytest.mark.parametrize("fcf_q,ni_q,equity,price", [
    (100.0, 80.0, 1_000.0, 100.0),      # modest business
    (10_000.0, 8_000.0, 100_000.0, 100.0),  # 100× more cash flow, same price
    (1.0, 1.0, 10.0, 100.0),            # almost no cash flow, same price
])
def test_valuation_upside_is_always_zero(fcf_q, ni_q, equity, price):
    """FIXED (WP-B2): the `scale = current_price / avg(candidates)` rescale is
    gone. Fair values are now per-share (enterprise estimate / shares
    outstanding), so the band is an independent estimate: the upside is a real
    number and the band no longer straddles the price by construction."""
    out = _valuation(fcf_q, ni_q, equity, price)
    assert out["current_price"] == price
    assert out["upside_pct"] is not None
    assert out["fair_value_low"] <= out["fair_value_high"]
    # The band is NOT pinned to the price any more.
    assert not (out["fair_value_low"] == out["fair_value_high"] == price)


@pytest.mark.django_db
def test_valuation_upside_tracks_fundamentals():
    """Different cash flows at the SAME price must give different upside."""
    poor = _valuation(1.0, 1.0, 10.0, 100.0)
    rich = _valuation(10_000.0, 8_000.0, 100_000.0, 100.0)
    assert poor["upside_pct"] < 0 < rich["upside_pct"]
    assert poor["fair_value_high"] < 100.0 < rich["fair_value_low"]


@pytest.mark.django_db
def test_valuation_without_shares_outstanding_reports_none_not_zero():
    """No share count in the fundamentals payload ⇒ no per-share fair value.
    The node must say so (upside_pct=None + a note), never emit a fake 0."""
    out = _valuation(100.0, 80.0, 1_000.0, 100.0, shares=None)
    assert out["upside_pct"] is None
    assert out["fair_value_low"] is None and out["fair_value_high"] is None
    assert out["dcf_fair_value"] is None
    assert "shares-outstanding" in out["notes"]
    assert out["current_price"] == 100.0


# ---------------------------------------------------------------------------
# F-K: macro classifier keys inflation off the CPI index LEVEL
# ---------------------------------------------------------------------------

def test_macro_inflation_regime_is_a_function_of_calendar_time_not_inflation():
    """FIXED: the inflation regime is the CPI YoY RATE, not the index level.

    CPIAUCSL is an index (1982-84 = 100) that only ever rises with time, so the
    old level thresholds ("> 320 = high") read high inflation permanently from
    ~2025 on and read the +7.5% YoY of Jan-2022 as "low"."""
    from hedgefund_agents.macro.macro_agent import classify_regime

    class _Obs:
        def __init__(self, v):
            self.value = v

    def regime(cpi_level, year_ago=None):
        prior = {"CPIAUCSL": _Obs(year_ago)} if year_ago is not None else None
        return classify_regime({"CPIAUCSL": _Obs(cpi_level)}, prior)["inflation_regime"]

    # 1. A bare LEVEL no longer decides anything. Without a year-ago print there
    #    is no inflation *rate* to read, so every level lands on the same
    #    neutral default instead of being ranked by calendar time.
    assert regime(281.1) == regime(296.3) == regime(330.0) == "moderate"

    # 2. A real YoY rate decides — and the SAME level classifies differently
    #    depending on it, which the level rule could never express.
    assert regime(296.3, year_ago=271.7) == "high"      # +9.1% — the Jun-2022 peak
    assert regime(330.0, year_ago=320.0) == "moderate"  # +3.1%
    assert regime(330.0, year_ago=327.0) == "low"       # +0.9%

    # 3. ...so it is no longer monotone in the level: a LOW 1980 level with 13.5%
    #    YoY is "high", and a HIGH 2026 level with ~1% YoY is "low".
    assert regime(82.4, year_ago=72.6) == "high"


# ---------------------------------------------------------------------------
# F-G: Anthropic 401/402/404 degrade silently instead of ModelUnavailable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_anthropic_auth_failure_degrades_persona_instead_of_failing_run(settings):
    from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant
    from hedgefund_agents.llm.adapters.anthropic import AnthropicClient
    from hedgefund_agents.personas.buffett import run_buffett

    settings.BLOCK_ANTHROPIC = False
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"type": "error",
                                         "error": {"type": "authentication_error",
                                                   "message": "invalid x-api-key"}})

    client = AnthropicClient(
        api_key="bad", http=httpx.Client(transport=httpx.MockTransport(handler))
    )

    class _NoFilings:
        def get_recent_filings(self, *a, **k):
            return []

    state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": None,
             "filings_provider": _NoFilings(),
             "model_overrides": {"buffett": "anthropic:claude-haiku-4-5-20251001"}}
    from apps.backtests.exceptions import ModelUnavailable

    with patch("hedgefund_agents.personas._base.get_llm", return_value=client), \
         patch("hedgefund_agents.personas._base.record_llm_call"):
        # FIXED (WP-B2): a permanently-broken key is a config error, not one
        # flaky agent. It now raises ModelUnavailable — the same class the
        # OpenRouter adapter raises — which wrap_backtest_tolerant re-raises,
        # so the run FAILS with an actionable message instead of completing
        # DONE with a silently all-neutral/hold council.
        with pytest.raises(ModelUnavailable) as ei:
            wrap_backtest_tolerant(run_buffett, "buffett")(state)
    assert ei.value.status_code == 401
    assert calls["n"] == 1                       # not retried


@pytest.mark.django_db
@pytest.mark.parametrize("status,exc_name", [
    (429, "RateLimited"),                        # rate limit
    (529, "RateLimited"),                        # Anthropic "overloaded"
])
def test_anthropic_capacity_errors_raise_the_retryable_class(settings, status, exc_name):
    from apps.backtests import exceptions as exc_mod
    from hedgefund_agents.llm.adapters import anthropic as anth

    settings.BLOCK_ANTHROPIC = False
    monkey = patch.object(anth.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"type": "error", "error": {"type": "overloaded"}})

    client = anth.AnthropicClient(
        api_key="k", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with monkey, pytest.raises(getattr(exc_mod, exc_name)):
        client.complete(model="claude-haiku-4-5-20251001",
                        messages=[Message("user", "x")])


@pytest.mark.django_db
def test_anthropic_5xx_still_degrades_per_agent(settings):
    """A transient upstream blip keeps the old per-agent self-heal (same as
    OpenRouter, which raises a plain HTTPStatusError for terminal 5xx)."""
    from hedgefund_agents.llm.adapters import anthropic as anth

    settings.BLOCK_ANTHROPIC = False

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    client = anth.AnthropicClient(
        api_key="k", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with patch.object(anth.time, "sleep", lambda *_: None), \
         pytest.raises(httpx.HTTPStatusError):
        client.complete(model="claude-haiku-4-5-20251001",
                        messages=[Message("user", "x")])


# ---------------------------------------------------------------------------
# F-L: the sentiment node is dead in production (no caller sets state["news"])
# ---------------------------------------------------------------------------

class _SentimentLLM:
    provider = "fake"

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
        from hedgefund_agents.outputs import SentimentOutput

        self.prompts.append(" ".join(m.content for m in messages if m.role == "user"))
        return LLMResponse(
            text=SentimentOutput(score=-0.6, top_drivers=["guidance cut"]).model_dump_json(),
            model=model, provider="fake",
        )


@pytest.mark.django_db
def test_sentiment_node_always_neutral_with_execute_run_state():
    """FIXED (WP-B2): execute_run now seeds `state["news"]`, and the node also
    falls back to the ticker's point-in-time NewsItem rows / the news_digest
    output — so it produces a real score instead of a constant 0.0."""
    import datetime as _dt
    import inspect

    from django.utils import timezone

    from apps.data.models import NewsItem
    from apps.runs import tasks
    from hedgefund_agents.analytical.sentiment import run_sentiment

    src = inspect.getsource(tasks.execute_run)
    assert '"news"' in src                        # initial_state now carries the news batch

    as_of = dt.date(2026, 6, 1)
    NewsItem.objects.create(
        ticker="AAPL", published_at=timezone.make_aware(_dt.datetime(2026, 5, 30, 12, 0)),
        headline="AAPL cuts full-year guidance", source="reuters", provider="tiingo",
        url="https://example.test/1", summary="Management cut FY guidance.",
    )
    llm = _SentimentLLM()
    with patch("hedgefund_agents.analytical.sentiment.get_llm", return_value=llm), \
         patch("hedgefund_agents.analytical.sentiment.record_llm_call"):
        out = run_sentiment({"ticker": "AAPL", "as_of_date": as_of})
    assert out["sentiment"]["score"] == -0.6
    assert out["sentiment"]["top_drivers"] == ["guidance cut"]
    assert "cuts full-year guidance" in llm.prompts[0]
    assert "<<<UNTRUSTED" in llm.prompts[0]          # still fenced


@pytest.mark.django_db
def test_sentiment_falls_back_to_the_news_digest_output():
    from hedgefund_agents.analytical.sentiment import run_sentiment

    llm = _SentimentLLM()
    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1),
        "news_digest": {
            "digest": "Guidance cut and an SEC inquiry.",
            "material_events": [{"headline": "SEC opens inquiry", "tag": "regulatory"}],
            "sentiment_drivers": ["guidance cut"],
        },
    }
    with patch("hedgefund_agents.analytical.sentiment.get_llm", return_value=llm), \
         patch("hedgefund_agents.analytical.sentiment.record_llm_call"):
        out = run_sentiment(state)
    assert out["sentiment"]["score"] == -0.6
    assert "SEC opens inquiry" in llm.prompts[0]


@pytest.mark.django_db
def test_sentiment_still_neutral_and_cheap_with_no_news_at_all():
    from hedgefund_agents.analytical.sentiment import run_sentiment

    with patch("hedgefund_agents.analytical.sentiment.get_llm") as llm:
        out = run_sentiment({"ticker": "NONEWS", "as_of_date": dt.date(2026, 6, 1)})
    assert out == {"sentiment": {"score": 0.0, "top_drivers": []}}
    assert not llm.called


@pytest.mark.django_db
def test_sentiment_node_never_makes_its_own_provider_calls():
    """It is a sibling of news_digest in the parallel fan-out — a fetch here
    would double the news quota and add network latency to the critical path.
    execute_run does the single fetch (see _seed_news_batch)."""
    from hedgefund_agents.analytical.sentiment import run_sentiment

    with patch("hedgefund_agents.analytical.sentiment.get_llm"), \
         patch("apps.data.providers.factory.get_news_service") as svc:
        run_sentiment({"ticker": "COLD", "as_of_date": dt.date(2026, 6, 1)})
    assert not svc.called


# ---------------------------------------------------------------------------
# F-M: screener ranks SHA1-random "synthetic" features next to real ones
# ---------------------------------------------------------------------------

def test_screener_ranks_synthetic_no_data_tickers_over_real_ones():
    """FIXED: compute_features no longer fabricates features from
    sha1(ticker|date) for a ticker with NO price data. Such a row comes back
    ``available=False`` / ``synthetic=True`` with no momentum, fundamentals or
    news-negativity at all, both scores sit at the sentinel floor, and
    run_screener DROPS it from the ranking (reporting it under
    ``excluded_no_data``). A real name in a 20% drawdown can no longer lose its
    short slot to tickers that have no data."""
    from hedgefund_agents.screener.features import (
        DEFAULT_WEIGHTS,
        compute_features,
        long_score,
        short_score,
    )
    from hedgefund_agents.screener.screener_agent import run_screener

    class _Prov:
        def get_daily_bars(self, ticker, start, end, *, as_of):
            if ticker == "REALFLAT":
                return [_Bar(100.0)] * 260                         # flat
            if ticker == "REALDOWN":
                return [_Bar(125.0)] * 200 + [_Bar(100.0)] * 60    # -20% from high
            return []                                               # no data at all

    as_of = dt.date(2026, 6, 1)
    flat = compute_features("REALFLAT", "Tech", as_of, provider=_Prov())
    down = compute_features("REALDOWN", "Tech", as_of, provider=_Prov())
    fakes = [compute_features(f"NODATA{i}", "Tech", as_of, provider=_Prov()) for i in range(20)]
    assert flat.available is True and flat.synthetic is False
    assert all(f.available is False and f.synthetic is True for f in fakes)
    # No invented fundamentals / news on a no-data row (the real path never
    # populates them either) and nothing claimed as a measured signal.
    assert (flat.earnings_yield, flat.quality_roic, flat.news_negative_score) == (0.0, 0.0, 0.0)
    assert all(
        (f.earnings_yield, f.quality_roic, f.news_negative_score) == (0.0, 0.0, 0.0)
        and not f.real_signals
        for f in fakes
    )
    # ...and they are unrankable: both sides score the -1e9 sentinel, while the
    # two real names stay comparable.
    assert all(long_score(f, DEFAULT_WEIGHTS) < -1e8 for f in fakes)
    assert all(short_score(f, DEFAULT_WEIGHTS) < -1e8 for f in fakes)
    assert long_score(flat, DEFAULT_WEIGHTS) > -1e8
    assert short_score(down, DEFAULT_WEIGHTS) > -1e8

    members = ([("REALFLAT", "Tech"), ("REALDOWN", "Tech")]
               + [(f"NODATA{i}", "Tech") for i in range(20)])
    out = run_screener(members=members, as_of_date=as_of, top_k_longs=1, top_k_shorts=5,
                       provider=_Prov())
    assert out["universe_size_evaluated"] == 22
    assert out["universe_size_ranked"] == 2                 # only the real names
    assert out["excluded_no_data_count"] == 20
    assert set(out["excluded_no_data"]) == {f"NODATA{i}" for i in range(20)}
    longs = [c["ticker"] for c in out["long_candidates"]]
    shorts = [c["ticker"] for c in out["short_candidates"]]
    assert not any(t.startswith("NODATA") for t in longs + shorts)
    assert "REALDOWN" in shorts                             # the real name keeps its slot
    assert not any(c["features"]["synthetic"] for c in out["long_candidates"])


# ---------------------------------------------------------------------------
# F-P: fundamentals with NO data still asks the LLM → fabricated ratios
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_fundamentals_with_no_rows_still_calls_llm_and_keeps_its_numbers():
    """FIXED (WP-B2): run_fundamentals now has the same empty-data guard as
    technicals. An empty statement set (ETF, ADR, unknown ticker, thin FMP
    tier) short-circuits to `available=False` + an explicit note — no LLM call,
    no invented ratios flowing downstream as if measured."""
    from hedgefund_agents.analytical.fundamentals import run_fundamentals
    from hedgefund_agents.outputs import FundamentalsOutput

    prompts: list[str] = []

    class _LLM:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
            prompts.append(" ".join(m.content for m in messages if m.role == "user"))
            return LLMResponse(
                text=FundamentalsOutput(revenue_cagr_3y=0.12, gross_margin=0.45,
                                        operating_margin=0.30, fcf_margin=0.25, roic=0.28,
                                        debt_to_equity=0.4, quality_score=85,
                                        notes="high quality").model_dump_json(),
                model=model, provider="fake")

    class _NoData:
        def get_fundamentals(self, ticker, metrics, *, as_of, lookback_quarters=8):
            return []

    state = {"ticker": "SPY", "as_of_date": dt.date(2026, 6, 1), "data_provider": _NoData(),
             "ownership_provider": None}
    with patch("hedgefund_agents.analytical.fundamentals.get_llm", return_value=_LLM()), \
         patch("hedgefund_agents.analytical.fundamentals.record_llm_call"):
        out = run_fundamentals(state)["fundamentals"]
    assert prompts == []                            # no LLM call at all
    assert out["available"] is False
    assert out["quality_score"] == 0 and out["roic"] == 0.0
    assert "No financial statements available for SPY" in out["notes"]


@pytest.mark.django_db
def test_fundamentals_with_rows_is_unchanged_and_marked_available():
    from hedgefund_agents.analytical.fundamentals import run_fundamentals
    from hedgefund_agents.outputs import FundamentalsOutput

    class _LLM:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2,
                     json_mode=False):
            return LLMResponse(
                text=FundamentalsOutput(revenue_cagr_3y=0.12, gross_margin=0.45,
                                        operating_margin=0.30, fcf_margin=0.25, roic=0.28,
                                        debt_to_equity=0.4, quality_score=85,
                                        notes="high quality").model_dump_json(),
                model=model, provider="fake")

    state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1),
             "data_provider": _Provider(100.0, 80.0, 1_000.0, 100.0),
             "ownership_provider": None}
    with patch("hedgefund_agents.analytical.fundamentals.get_llm", return_value=_LLM()), \
         patch("hedgefund_agents.analytical.fundamentals.record_llm_call"):
        out = run_fundamentals(state)["fundamentals"]
    assert out["available"] is True and out["quality_score"] == 85
