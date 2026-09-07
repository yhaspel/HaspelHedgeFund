"""Adversarial review (reviewer: runsllm) — cost-accounting / budget-guard proofs.

Every test here documents a defect; each docstring names the finding.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth import get_user_model

from apps.backtests.exceptions import BudgetExceeded, ModelUnavailable
from hedgefund_agents._persist import record_llm_call
from hedgefund_agents.llm.client import LLMResponse, Message
from hedgefund_agents.llm.structured import call_structured
from hedgefund_agents.models import LLMCall
from hedgefund_agents.outputs import (
    CioOutput,
    FundamentalsOutput,
    NewsOutput,
    PersonaOutput,
    RiskOutput,
    SentimentOutput,
    TechnicalsOutput,
    ValuationOutput,
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(email="rev@example.test", password="x")


def _run(user, **kw):
    from apps.runs.models import Run

    return Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=dt.date(2026, 6, 1),
        status=Run.RUNNING, **kw,
    )


class _CountingClient:
    """Bills every request (cost_usd=0.01, 1500 tokens) but returns invalid
    JSON for the first `bad` attempts."""

    provider = "fake"

    def __init__(self, bad: int, good_text: str) -> None:
        self.bad = bad
        self.good_text = good_text
        self.calls = 0

    def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
        self.calls += 1
        text = "Sorry, I cannot help with that." if self.calls <= self.bad else self.good_text
        return LLMResponse(
            text=text, model=model, provider=self.provider,
            prompt_tokens=1000, completion_tokens=500, cost_usd=0.01, latency_ms=1,
        )


# ---------------------------------------------------------------------------
# F-A: call_structured retries are billed but never recorded
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_structured_retry_attempts_are_never_recorded_as_llmcalls(user):
    """FIXED (WP-B2): call_structured now hands back the successful attempt
    with every earlier BILLED attempt on `prior_attempts`, and record_llm_call
    writes an LLMCall row per attempt — so LLMCall / Run.total_cost_usd /
    /costs/summary/ and the budget guard all see the true spend."""
    run = _run(user)
    client = _CountingClient(bad=2, good_text=SentimentOutput(score=0.1).model_dump_json())
    parsed, resp = call_structured(
        client, model="m", schema=SentimentOutput,
        messages=[Message("user", "score this")],
    )
    assert client.calls == 3            # three billed requests …
    assert resp.attempts == 3
    record_llm_call(run_id=run.id, agent_name="sentiment", resp=resp)  # … what every node does
    run.refresh_from_db()
    assert LLMCall.objects.filter(run=run).count() == 3
    # True spend was 3 × $0.01 = $0.03 — and that is what the ledger says.
    assert run.total_cost_usd == Decimal("0.030000")
    assert sum(c.prompt_tokens for c in LLMCall.objects.filter(run=run)) == 3000


@pytest.mark.django_db
def test_single_attempt_still_writes_exactly_one_row(user):
    """No regression for the overwhelmingly common one-attempt case."""
    run = _run(user)
    client = _CountingClient(bad=0, good_text=SentimentOutput(score=0.1).model_dump_json())
    _, resp = call_structured(
        client, model="m", schema=SentimentOutput, messages=[Message("user", "x")],
    )
    assert client.calls == 1 and resp.attempts == 1
    record_llm_call(run_id=run.id, agent_name="sentiment", resp=resp)
    run.refresh_from_db()
    assert LLMCall.objects.filter(run=run).count() == 1
    assert run.total_cost_usd == Decimal("0.010000")


@pytest.mark.django_db
def test_three_failed_attempts_bill_but_record_nothing_and_bypass_budget(user):
    """FIXED (WP-B2): when all 3 attempts fail validation the node still never
    reaches its own record_llm_call — so the self-heal wrapper persists the
    billed attempts off the StructuredOutputError. The run's cap is now
    genuinely enforced instead of being bypassed by unrecorded spend."""
    from apps.backtests.exceptions import BudgetExceeded
    from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant
    from hedgefund_agents.personas.buffett import run_buffett

    run = _run(user, max_budget_usd=Decimal("0.005"))  # cap below ONE call
    client = _CountingClient(bad=99, good_text="")

    class _NoFilings:
        def get_recent_filings(self, *a, **k):
            return []

    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": run.id,
        "filings_provider": _NoFilings(),
    }
    with patch("hedgefund_agents.personas._base.get_llm", return_value=client):
        with pytest.raises(BudgetExceeded):        # the guard now fires
            wrap_backtest_tolerant(run_buffett, "buffett")(state)
    assert client.calls == 3
    assert LLMCall.objects.filter(run=run).count() == 3   # $0.03 spent, $0.03 recorded
    run.refresh_from_db()
    assert run.total_cost_usd == Decimal("0.030000")


@pytest.mark.django_db
def test_failed_attempts_are_recorded_and_the_node_still_degrades(user):
    """Same path with no budget cap: the spend is booked and the council still
    self-heals to a null signal rather than aborting the run."""
    from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant
    from hedgefund_agents.personas.buffett import run_buffett

    run = _run(user)
    client = _CountingClient(bad=99, good_text="")

    class _NoFilings:
        def get_recent_filings(self, *a, **k):
            return []

    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": run.id,
        "filings_provider": _NoFilings(),
    }
    with patch("hedgefund_agents.personas._base.get_llm", return_value=client):
        out = wrap_backtest_tolerant(run_buffett, "buffett")(state)
    assert out["buffett"]["_degraded"] is True
    assert LLMCall.objects.filter(run=run, agent_name="buffett").count() == 3
    run.refresh_from_db()
    assert run.total_cost_usd == Decimal("0.030000")


# ---------------------------------------------------------------------------
# F-B: news + CIO nodes swallow BudgetExceeded / ModelUnavailable
# ---------------------------------------------------------------------------

def _news_client(cost: float):
    class _C:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
            return LLMResponse(
                text=NewsOutput(ticker="AAPL", digest="real digest", sentiment_score=0.2)
                .model_dump_json(),
                model=model, provider="fake", prompt_tokens=10, completion_tokens=5,
                cost_usd=cost,
            )
    return _C()


@pytest.mark.django_db
def test_news_node_swallows_budget_exceeded(user):
    """FIXED (WP-B2): the blanket `except Exception` is narrowed to LLM/parse
    errors, so the mid-run budget abort raised inside record_llm_call now
    propagates and stops the run instead of becoming a 'skeletal digest' while
    the persona fan-out keeps spending."""
    from hedgefund_agents.news import news_agent

    run = _run(user, max_budget_usd=Decimal("0.50"))

    class _Svc:
        def fetch_and_persist(self, *a, **k):
            return []

    state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": run.id}
    with patch.object(news_agent, "get_news_service", return_value=_Svc()), \
         patch.object(news_agent, "_risk_factors_excerpt", return_value=""), \
         patch.object(news_agent, "get_llm", return_value=_news_client(1.00)), \
         pytest.raises(BudgetExceeded):
        news_agent.run_news(state)
    run.refresh_from_db()
    assert run.total_cost_usd == Decimal("1.000000") >= run.max_budget_usd


@pytest.mark.django_db
def test_news_node_swallows_model_unavailable():
    """FIXED (WP-B2): an out-of-credits / bad-key ModelUnavailable (which
    _node_fallback deliberately re-raises) is no longer masked by run_news."""
    from hedgefund_agents.news import news_agent

    class _Svc:
        def fetch_and_persist(self, *a, **k):
            return []

    class _Dead:
        provider = "openrouter"

        def complete(self, **kw):
            raise ModelUnavailable(model="x", status_code=402, body="Out of credits")

    state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": None}
    with patch.object(news_agent, "get_news_service", return_value=_Svc()), \
         patch.object(news_agent, "_risk_factors_excerpt", return_value=""), \
         patch.object(news_agent, "get_llm", return_value=_Dead()), \
         pytest.raises(ModelUnavailable):
        news_agent.run_news(state)


@pytest.mark.django_db
def test_news_node_still_degrades_on_a_parse_failure():
    """The narrowing must not cost the node its real self-heal: unparseable
    model output still yields the skeletal digest."""
    from hedgefund_agents.llm.structured import StructuredOutputError
    from hedgefund_agents.news import news_agent

    class _Svc:
        def fetch_and_persist(self, *a, **k):
            return []

    class _Junk:
        provider = "fake"

        def complete(self, **kw):
            raise StructuredOutputError(
                "bad json", LLMResponse(text="nope", model="m", provider="fake")
            )

    state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": None}
    with patch.object(news_agent, "get_news_service", return_value=_Svc()), \
         patch.object(news_agent, "_risk_factors_excerpt", return_value=""), \
         patch.object(news_agent, "get_llm", return_value=_Junk()):
        out = news_agent.run_news(state)
    assert out["news_digest"]["_freshness"]["fallback"] is True
    assert out["news_digest"]["digest"] == "(news synthesis unavailable for this run)"


@pytest.mark.django_db
def test_cio_node_swallows_budget_exceeded(user):
    """FIXED (WP-B2): same narrowing on the CIO node."""
    from hedgefund_agents.portfolio.cio import run_cio

    run = _run(user, max_budget_usd=Decimal("0.50"))

    class _C:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
            return LLMResponse(
                text=CioOutput(ticker="AAPL", action="buy", target_weight_pct=8.0,
                               outlook="ok", confidence=70).model_dump_json(),
                model=model, provider="fake", prompt_tokens=1, completion_tokens=1,
                cost_usd=1.00,
            )

    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": run.id,
        "decision": {"ticker": "AAPL", "action": "buy", "target_weight_pct": 8.0,
                     "target_quantity": 10.0, "aggregate_confidence": 70,
                     "rationale": "r", "dissenting_personas": []},
    }
    with patch("hedgefund_agents.portfolio.cio.get_llm", return_value=_C()), \
         pytest.raises(BudgetExceeded):
        run_cio(state)
    run.refresh_from_db()
    assert run.total_cost_usd >= run.max_budget_usd


# ---------------------------------------------------------------------------
# F-C: parallel persona fan-out overshoots the cap (race between nodes)
# ---------------------------------------------------------------------------

_LLM_TARGETS = [
    "hedgefund_agents.analytical.fundamentals.get_llm",
    "hedgefund_agents.analytical.technicals.get_llm",
    "hedgefund_agents.analytical.valuation.get_llm",
    "hedgefund_agents.analytical.sentiment.get_llm",
    "hedgefund_agents.risk.risk_manager.get_llm",
    "hedgefund_agents.personas._base.get_llm",
    "hedgefund_agents.portfolio.cio.get_llm",
]
_PERSIST_TARGETS = [
    "hedgefund_agents.analytical.fundamentals.record_llm_call",
    "hedgefund_agents.analytical.technicals.record_llm_call",
    "hedgefund_agents.analytical.valuation.record_llm_call",
    "hedgefund_agents.analytical.sentiment.record_llm_call",
    "hedgefund_agents.risk.risk_manager.record_llm_call",
    "hedgefund_agents.personas._base.record_llm_call",
    "hedgefund_agents.portfolio.cio.record_llm_call",
]


def test_parallel_persona_fanout_overshoots_budget_cap(monkeypatch):
    """The guard runs AFTER each call inside record_llm_call, but LangGraph
    executes the 8 persona nodes concurrently: every persona's LLM request is
    already in flight before the first one is recorded. With a cap that
    allows exactly ONE more call, all 8 persona calls are still billed."""
    from hedgefund_agents.analytical.sentiment import NewsBatch
    from hedgefund_agents.graphs import council
    from tests.test_council_integration import (
        _FakeDataProvider,
        _FakeFilingsProvider,
        _FakeOwnershipProvider,
    )

    nodes = dict(council.ANALYTICAL_NODES)
    nodes["macro"] = lambda s: {"macro": {"narrative": "stub"}}
    nodes["news_digest"] = lambda s: {"news_digest": {"digest": "stub"}}
    monkeypatch.setattr(council, "ANALYTICAL_NODES", nodes)

    persona_calls = {"n": 0}
    lock = threading.Lock()

    class _Fake:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
            sys_text = " ".join(m.content for m in messages if m.role == "system").lower()
            if "fundamentals" in sys_text and "ratios" in sys_text:
                text = FundamentalsOutput(revenue_cagr_3y=0.1, gross_margin=0.4,
                                          operating_margin=0.2, fcf_margin=0.2, roic=0.2,
                                          debt_to_equity=0.5, quality_score=80,
                                          notes="x").model_dump_json()
            elif "technical analyst" in sys_text:
                text = TechnicalsOutput(regime="range", momentum_1m=0, momentum_3m=0,
                                        momentum_6m=0, rsi_14=50, atr_pct=1,
                                        signal="neutral", confidence=50).model_dump_json()
            elif "valuation analyst" in sys_text:
                text = ValuationOutput(fair_value_low=1, fair_value_high=2, current_price=1,
                                       upside_pct=0, most_sensitive_assumption="x"
                                       ).model_dump_json()
            elif "risk manager" in sys_text:
                text = RiskOutput(rationale="ok", max_position_pct_for_this_trade=0.1
                                  ).model_dump_json()
            else:  # persona
                with lock:
                    persona_calls["n"] += 1
                time.sleep(0.4)  # a real call takes seconds; all 8 overlap
                text = PersonaOutput(signal="bullish", confidence=80, thesis="t",
                                     key_risks=[]).model_dump_json()
            return LLMResponse(text=text, model=model, provider="fake",
                               prompt_tokens=1, completion_tokens=1, cost_usd=1.0)

    recorded = {"n": 0}

    def _record(*, run_id=None, agent_name, resp, **kw):
        # Emulates _persist.record_llm_call with a $1 cap: the FIRST persona
        # record trips the guard.
        with lock:
            recorded["n"] += 1
            if agent_name in council.PERSONA_NODES:
                raise BudgetExceeded(1.0, 1.0, recorded["n"], recorded["n"])

    graph = council.build_council_graph()  # all 8 personas
    state = {
        "ticker": "AAPL", "as_of_date": dt.date(2024, 12, 31),
        "data_provider": _FakeDataProvider(), "filings_provider": _FakeFilingsProvider(),
        "ownership_provider": _FakeOwnershipProvider(), "news": NewsBatch.empty(),
    }
    import contextlib
    with contextlib.ExitStack() as stack:
        for t in _LLM_TARGETS:
            stack.enter_context(patch(t, return_value=_Fake()))
        for t in _PERSIST_TARGETS:
            stack.enter_context(patch(t, side_effect=_record))
        with pytest.raises(BudgetExceeded):
            graph.invoke(state)
    time.sleep(1.0)  # let in-flight threads finish
    # Cap allowed ONE more persona call; on this 2-vCPU box 7 of the 8 persona
    # requests were already in flight (the exact number is bounded only by the
    # executor's thread pool, not by the budget).
    assert persona_calls["n"] >= 4, persona_calls


# ---------------------------------------------------------------------------
# F-F: a :free route self-heals onto the PAID last resort (default settings)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_free_route_self_heals_onto_paid_last_resort(settings):
    """FIXED (WP-B2): the static last resort now obeys the same price-class
    discipline as the catalog candidates, so a `:free` chain can only ever hop
    to other `:free` routes. settings.OPENROUTER_PAID_FALLBACK stays the one
    documented (opt-in, off by default) paid escape hatch."""
    from apps.backtests.exceptions import ModelUnavailable
    from hedgefund_agents.llm.adapters import openrouter as orm

    settings.LLM_FREE_ONLY = False           # base.py default
    settings.OPENROUTER_PAID_FALLBACK = False  # the documented paid opt-in is OFF
    settings.LLM_SELF_HEAL = True
    settings.LLM_REQUIRE_KNOWN_PRICES = True

    free = "nvidia/nemotron-3-super-120b-a12b:free"   # registry._BLOCKED_FALLBACK
    model, tried, chain = free, (), []
    while (nxt := orm._next_heal_target(model, tried)) is not None and len(chain) < 6:
        chain.append(nxt)
        tried, model = tried + (model,), nxt
    # The seeded "dev" tier holds 4 :free models — the chain is the 3 free
    # siblings and then it STOPS. No paid rung.
    assert chain and all(c.endswith(":free") for c in chain), chain

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        model = _json.loads(request.content)["model"]
        seen.append(model)
        if model.endswith(":free"):
            return httpx.Response(404, json={"error": {"message": "No endpoints found"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100_000, "completion_tokens": 50_000},
        })

    client = orm.OpenRouterClient(api_key="k",
                                  http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelUnavailable):
        client.complete(model=free, messages=[Message("user", "hi")])
    assert seen[0] == free
    assert all(m.endswith(":free") for m in seen), seen   # nothing paid was ever billed


@pytest.mark.django_db
def test_paid_route_never_downgrades_onto_a_free_one(settings):
    """The discipline is symmetric — a paid chain must not silently land on a
    :free route (different rate-limit pool, different quality tier)."""
    from hedgefund_agents.llm.adapters import openrouter as orm

    settings.LLM_FREE_ONLY = True            # makes the last resort :free
    paid = "meta-llama/llama-3.3-70b-instruct"
    model, tried, chain = paid, (), []
    while (nxt := orm._next_heal_target(model, tried)) is not None and len(chain) < 6:
        chain.append(nxt)
        tried, model = tried + (model,), nxt
    assert not any(c.endswith(":free") for c in chain), chain


# ---------------------------------------------------------------------------
# F-B2: unknown-priced model → strict raise AFTER the billed request
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_unknown_price_raises_after_spend_and_node_degrades(settings, user):
    """estimate_cost runs after the HTTP round-trip. In strict mode (the prod
    default) the UnknownModelPriceError is raised post-spend, no LLMCall row is
    written, and the self-heal wrapper degrades the node — so the run keeps
    calling the unpriced model for every other agent, all unrecorded."""
    from hedgefund_agents.graphs._node_fallback import wrap_backtest_tolerant
    from hedgefund_agents.llm.adapters import openrouter as orm
    from hedgefund_agents.llm.pricing import UnknownModelPriceError

    settings.LLM_REQUIRE_KNOWN_PRICES = True
    settings.LLM_SELF_HEAL = False
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"score": 0.1, "top_drivers": []}'},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5000, "completion_tokens": 500},
        })

    client = orm.OpenRouterClient(
        api_key="k", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(UnknownModelPriceError):
        client.complete(model="some-vendor/unpriced-model", messages=[Message("user", "x")])
    assert hits["n"] == 1  # the request was made (and billed) before the raise

    run = _run(user)

    def node(state):
        resp = client.complete(model="some-vendor/unpriced-model", messages=[Message("user", "x")])
        record_llm_call(run_id=run.id, agent_name="sentiment", resp=resp)
        return {"sentiment": {"score": 0.1}}

    out = wrap_backtest_tolerant(node, "sentiment")({"ticker": "AAPL"})
    assert out["sentiment"]["_degraded"] is True
    assert hits["n"] == 2
    assert LLMCall.objects.filter(run=run).count() == 0
