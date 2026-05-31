"""Full council graph: mocked LLM + mocked data providers.

Verifies:
  • All 4 analytical agents + selected personas + risk + PM run end-to-end.
  • PM final decision is well-formed.
  • When all personas bullish + no veto → action=buy.
  • Risk veto → action=hold regardless of personas.
  • Mixed personas → dissent captured.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import time
from decimal import Decimal
from unittest.mock import patch

import pytest

from hedgefund_agents.analytical.sentiment import NewsBatch
from hedgefund_agents.graphs.council import build_council_graph
from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.outputs import (
    CioOutput,
    FundamentalsOutput,
    PersonaOutput,
    RiskOutput,
    SentimentOutput,
    TechnicalsOutput,
    ValuationOutput,
)

_LLM_PATCH_TARGETS = [
    "hedgefund_agents.analytical.fundamentals.get_llm",
    "hedgefund_agents.analytical.technicals.get_llm",
    "hedgefund_agents.analytical.valuation.get_llm",
    "hedgefund_agents.analytical.sentiment.get_llm",
    "hedgefund_agents.risk.risk_manager.get_llm",
    "hedgefund_agents.personas._base.get_llm",
    "hedgefund_agents.portfolio.cio.get_llm",
]


@pytest.fixture(autouse=True)
def _stub_macro_and_news(monkeypatch):
    """Replace macro + news nodes with no-op stubs so the council graph can run
    offline. The P2b agents have their own dedicated tests."""
    from hedgefund_agents.graphs import council

    def _macro_stub(state):
        return {"macro": {
            "as_of_date": state["as_of_date"].isoformat(),
            "growth_quadrant": "expansion", "inflation_regime": "moderate",
            "yield_curve_state": "normal", "policy_stance": "neutral",
            "narrative": "stub", "sector_implications": {},
        }}

    def _news_stub(state):
        return {"news_digest": {
            "ticker": state["ticker"], "digest": "stub",
            "material_events": [], "risk_factor_highlights": [],
            "sentiment_score": 0.0, "sentiment_drivers": [],
        }}

    nodes = dict(council.ANALYTICAL_NODES)
    nodes["macro"] = _macro_stub
    nodes["news_digest"] = _news_stub
    monkeypatch.setattr(council, "ANALYTICAL_NODES", nodes)


_PERSIST_TARGETS = [
    "hedgefund_agents.analytical.fundamentals.record_llm_call",
    "hedgefund_agents.analytical.technicals.record_llm_call",
    "hedgefund_agents.analytical.valuation.record_llm_call",
    "hedgefund_agents.analytical.sentiment.record_llm_call",
    "hedgefund_agents.risk.risk_manager.record_llm_call",
    "hedgefund_agents.personas._base.record_llm_call",
    "hedgefund_agents.portfolio.cio.record_llm_call",
]


@contextlib.contextmanager
def patch_all_llms(fake):
    """Patch every agent's get_llm + skip DB persistence (sqlite + parallel
    fan-out collide on the in-memory DB; the persistence path is covered by
    test_full_run_aapl_qwen in test_integration.py)."""
    with contextlib.ExitStack() as stack:
        for tgt in _LLM_PATCH_TARGETS:
            stack.enter_context(patch(tgt, return_value=fake))
        for tgt in _PERSIST_TARGETS:
            stack.enter_context(patch(tgt))
        yield


# ---- fake providers -------------------------------------------------------

class _FakeBar:
    def __init__(self, close: float) -> None:
        self.date = dt.date(2024, 12, 31)
        self.open = self.high = self.low = self.close = Decimal(str(close))
        self.adjusted_close = self.close
        self.volume = 1_000_000


class _FakeRow:
    def __init__(self, metric: str, value: float, period_end: dt.date) -> None:
        self.metric = metric
        self.value = Decimal(str(value))
        self.period_end = period_end
        self.as_of_date = period_end
        self.ticker = "AAPL"


class _FakeDataProvider:
    name = "fake"

    def get_daily_bars(self, ticker, start, end, *, as_of):
        # 250 bars trending up.
        bars = []
        d = end
        for i in range(250):
            bars.append(_FakeBar(close=100.0 + i * 0.2))
            d = d - dt.timedelta(days=1)
        return list(reversed(bars))

    def get_fundamentals(self, ticker, metrics, *, as_of, lookback_quarters=8):
        rows = []
        base = dt.date(2024, 12, 1)
        for q in range(min(lookback_quarters, 12)):
            # step back q*90 days, snap to 1st of month.
            p = base - dt.timedelta(days=90 * q)
            p = p.replace(day=1)
            for m in metrics:
                rows.append(_FakeRow(m, 1_000_000.0 * (1 + q * 0.02), p))
        return rows


class _FakeFilingsProvider:
    name = "fake"

    def get_recent_filings(self, ticker, *, as_of, form_types, limit=4):
        return []


class _FakeOwnershipProvider:
    # Returns None so the Fundamentals prompt is unchanged and the existing
    # AAPL cassettes replay without re-recording (no new LLM call).
    name = "fake"

    def get_issuer_ownership(self, ticker, *, as_of):
        return None

    def get_filer_portfolio(self, filer_cik, *, as_of):
        return None


# ---- fake LLM -------------------------------------------------------------

def _persona_payload(signal: str = "bullish", confidence: int = 80) -> str:
    return PersonaOutput(
        signal=signal, confidence=confidence,
        thesis="Strong fundamentals and durable cash flow profile support thesis.",
        key_risks=["regulatory", "concentration"],
        intrinsic_value_estimate=200.0, margin_of_safety_pct=20.0,
    ).model_dump_json()


def _make_fake_llm(persona_signal: str = "bullish", risk_veto: bool = False):
    class _Fake:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
            sys_text = " ".join(m.content for m in messages if m.role == "system").lower()
            if "fundamentals" in sys_text and "ratios" in sys_text:
                text = FundamentalsOutput(
                    revenue_cagr_3y=0.08, gross_margin=0.42, operating_margin=0.28,
                    fcf_margin=0.25, roic=0.30, debt_to_equity=1.5,
                    quality_score=85, notes="solid",
                ).model_dump_json()
            elif "technical analyst" in sys_text:
                text = TechnicalsOutput(
                    regime="trending_up", momentum_1m=0.05, momentum_3m=0.10,
                    momentum_6m=0.15, rsi_14=60.0, atr_pct=2.0,
                    signal="bullish", confidence=70,
                ).model_dump_json()
            elif "valuation analyst" in sys_text:
                text = ValuationOutput(
                    dcf_fair_value=120.0, multiples_fair_value=140.0,
                    residual_income_fair_value=130.0,
                    fair_value_low=120.0, fair_value_high=140.0,
                    current_price=100.0, upside_pct=30.0,
                    most_sensitive_assumption="WACC",
                ).model_dump_json()
            elif "sentiment scorer" in sys_text:
                text = SentimentOutput(score=0.2, top_drivers=["earnings beat"]).model_dump_json()
            elif "risk manager" in sys_text:
                text = RiskOutput(
                    hard_caps_applied=[], max_position_pct_for_this_trade=0.10,
                    stop_loss_pct=0.08, veto=risk_veto,
                    rationale="ok",
                ).model_dump_json()
            elif "chief investment officer" in sys_text:
                # CIO ratifies PM unchanged by default.
                user_block = next(
                    (m.content for m in messages if m.role == "user"), ""
                )
                # Pull PM action/weight/qty from the context the CIO sees.
                import json as _json
                import re as _re
                m = _re.search(r'"action":\s*"(\w+)"', user_block)
                action = m.group(1) if m else "hold"
                w = _re.search(r'"target_weight_pct":\s*([0-9.]+)', user_block)
                q = _re.search(r'"target_quantity":\s*([0-9.]+)', user_block)
                text = CioOutput(
                    ticker="AAPL", action=action,  # type: ignore[arg-type]
                    target_weight_pct=float(w.group(1)) if w else 0.0,
                    target_quantity=float(q.group(1)) if q else 0.0,
                    overrode_pm=False, outlook="ratify",
                    confidence=70,
                ).model_dump_json()
                _ = _json  # noqa
            else:
                # persona
                text = _persona_payload(signal=persona_signal)
            return LLMResponse(
                text=text, model=model, provider=self.provider,
                prompt_tokens=10, completion_tokens=5, cost_usd=0.0, latency_ms=1,
            )

    return _Fake()


def _initial_state(**extra):
    return {
        "ticker": "AAPL",
        "as_of_date": dt.date(2024, 12, 31),
        "data_provider": _FakeDataProvider(),
        "filings_provider": _FakeFilingsProvider(),
        "ownership_provider": _FakeOwnershipProvider(),
        "news": NewsBatch.empty(),
        **extra,
    }


# ---- tests ----------------------------------------------------------------

@pytest.mark.django_db
def test_full_council_runs_end_to_end() -> None:
    graph = build_council_graph()
    with patch_all_llms(_make_fake_llm()):
        out = graph.invoke(_initial_state())

    for k in ("fundamentals", "technicals", "valuation", "sentiment", "risk", "decision"):
        assert k in out, f"missing {k}"
    for persona in (
        "buffett", "munger", "graham", "wood",
        "druckenmiller", "burry", "damodaran", "lynch",
    ):
        assert persona in out, f"missing persona {persona}"
    d = out["decision"]
    assert d["ticker"] == "AAPL"
    assert d["action"] in {"buy", "hold", "sell"}


@pytest.mark.django_db
def test_all_bullish_personas_yields_buy() -> None:
    graph = build_council_graph(personas=["buffett", "munger", "graham", "wood", "druckenmiller"])
    with patch_all_llms(_make_fake_llm("bullish")):
        out = graph.invoke(_initial_state())
    d = out["decision"]
    assert d["action"] == "buy"
    cap = out["risk"]["max_position_pct_for_this_trade"]
    assert d["target_weight_pct"] / 100.0 <= cap + 1e-6


@pytest.mark.django_db
def test_risk_veto_forces_hold() -> None:
    graph = build_council_graph(personas=["buffett", "munger", "graham"])
    fake = _make_fake_llm("bullish", risk_veto=True)
    with patch_all_llms(fake):
        from hedgefund_agents.risk.risk_manager import StubPortfolio
        out = graph.invoke(_initial_state(portfolio=StubPortfolio(drawdown_pct=0.30)))
    d = out["decision"]
    assert d["action"] == "hold"
    assert d["target_quantity"] == 0.0


@pytest.mark.django_db
def test_dissent_recorded_when_one_persona_disagrees() -> None:
    graph = build_council_graph(personas=["buffett", "munger", "graham", "wood", "burry"])

    bull = _make_fake_llm("bullish")
    bear = _make_fake_llm("bearish")

    class _RouteFake:
        provider = "fake"

        def complete(self, **kwargs):
            persona_sys = next(
                (m.content for m in kwargs["messages"]
                 if m.role == "system" and "channelling" in m.content.lower()),
                "",
            ).lower()
            if "burry" in persona_sys or "fragility" in persona_sys:
                return bear.complete(**kwargs)
            return bull.complete(**kwargs)

    route = _RouteFake()
    with patch_all_llms(route):
        out = graph.invoke(_initial_state())
    d = out["decision"]
    dissent_names = {p["name"] for p in d["dissenting_personas"]}
    assert "burry" in dissent_names
    assert d["action"] == "buy"  # 4 bullish vs 1 bearish


@pytest.mark.django_db
def test_parallel_fanout_is_concurrent() -> None:
    """If fan-out is parallel, total wall-time ≈ slowest agent, not sum."""
    graph = build_council_graph(personas=["buffett", "munger", "graham"])

    inner = _make_fake_llm("bullish")

    class _SlowFake:
        provider = "fake"

        def complete(self, **kwargs):
            time.sleep(0.05)
            return inner.complete(**kwargs)

    slow = _SlowFake()
    with patch_all_llms(slow):
        t0 = time.perf_counter()
        graph.invoke(_initial_state())
        elapsed = time.perf_counter() - t0
    # Pure serial would be ~ (4 analytical + 3 persona + 1 risk) * 0.05 = 0.40s.
    # Parallel should be roughly (1 analytical + 1 persona + 1 risk) * 0.05 = 0.15s.
    # Allow generous slack for CI jitter; mainly assert we're well below serial.
    assert elapsed < 0.35, f"fan-out doesn't look parallel: {elapsed:.3f}s"
