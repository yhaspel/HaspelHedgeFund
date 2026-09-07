"""Review (portmisc): leaderboard — findings, now FIXED (wave 3, WP P1).

Each test below used to assert the bug; it now asserts the corrected
behaviour. The original finding is kept in the docstring so the regression is
readable.

F-overlap      : strategy scorecards compounded `marked_snapshot.since_as_of_pct`
                 (an OPEN-ENDED cumulative return from each cycle's as_of to the
                 snapshot time) as if it were a sequence of disjoint per-cycle
                 returns. FIXED: the series is rebuilt over disjoint intervals
                 (cycle N -> cycle N+1), so it telescopes to the real move.
F-overflow     : the same annualisation ((1+mean)^252) overflowed the
                 `annualised_return_pct` column for a very ordinary mean of 6% —
                 the nightly recompute then raised after having already deleted
                 the day's rows. FIXED: CAGR over the observed span, and every
                 decimal is clamped to its column at the write boundary.
F-rerun-gaming : re-running the same ticker/date N times yielded N "independent"
                 decisions. FIXED: deduped to one observation per
                 (agent, version, model, ticker, as_of).
F-hindsight    : a run backdated via `as_of_date` (user-settable) was scored in
                 the created_at window. FIXED: windows are on `as_of_date`.
F-xtenant      : agent scorecards + the per-decision drill-down were global —
                 user B saw user A's tickers/dates/models. FIXED: scorecards
                 carry an owner and both views filter on the caller.
F-nav-cash     : council_net_value_usd multiplied by `portfolio.cash_balance`,
                 not equity. FIXED: `_strategy_nav` marks the positions too.
F-n+1          : agent_decision_detail did one DailyBar query per decision with
                 no pagination. FIXED: one batched query + limit/offset.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.data.models import DailyBar
from apps.leaderboard import compute
from apps.leaderboard.models import AgentScorecard, StrategyScorecard
from apps.portfolios import cycle_mark
from apps.portfolios.models import (
    Portfolio,
    PortfolioStrategy,
    PortfolioTarget,
    Position,
    Universe,
)
from apps.runs.models import AgentMessage, Run

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="lb-a@x.test", password="pw-fake-123456789")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(email="lb-b@x.test", password="pw-fake-123456789")


def _bars(ticker, start: dt.date, closes):
    for i, c in enumerate(closes):
        DailyBar.objects.create(
            ticker=ticker, date=start + dt.timedelta(days=i),
            open=c, high=c, low=c, close=c, adjusted_close=c, volume=1000, source="fmp",
        )


def _persona_run(user, ticker, persona, signal, confidence, as_of, model="m1", version="v1"):
    run = Run.objects.create(
        user=user, tickers=[ticker], as_of_date=as_of, status=Run.DONE,
        model_overrides={persona: model}, agent_versions={persona: version},
    )
    AgentMessage.objects.create(
        run=run, agent_name=persona,
        parsed_output={"signal": signal, "confidence": confidence},
    )
    return run


def _strategy(user, name="s", cash="100000"):
    universe, _ = Universe.objects.get_or_create(name="pm-uni", defaults={"is_active": True})
    portfolio = Portfolio.objects.create(
        user=user, name=f"{name}-book", kind="strategy", cash_balance=Decimal(cash),
    )
    return PortfolioStrategy.objects.create(
        user=user, name=name, kind=PortfolioStrategy.KIND_LONG_ONLY,
        universe=universe, portfolio=portfolio,
    )


class _DbBarProvider:
    """Serves whatever DailyBar rows exist (what the real provider does once
    the rows are cached)."""

    def get_daily_bars(self, ticker, start, end, *, as_of):
        from apps.data.interfaces import Bar

        rows = DailyBar.objects.filter(
            ticker=ticker, source="fmp", date__gte=start, date__lte=min(end, as_of),
        ).order_by("date")
        return [
            Bar(ticker=r.ticker, date=r.date, open=r.open, high=r.high, low=r.low,
                close=r.close, adjusted_close=r.adjusted_close, volume=r.volume)
            for r in rows
        ]


# ---------------------------------------------------------------------------
# F-overlap
# ---------------------------------------------------------------------------


def test_strategy_returns_use_disjoint_windows(user, monkeypatch):
    """A 100%-long AAPL book re-issued on three consecutive days while AAPL
    goes 100 -> 103 has made +3%, and the scorecard now says +3%.

    The per-cycle snapshots are still open-ended cumulative windows (they are
    what the cycle page shows); the scorecard no longer chain-multiplies them.
    """
    monkeypatch.setattr(cycle_mark, "get_fmp_provider", lambda user=None: _DbBarProvider())
    today = timezone.localdate()
    d0 = today - dt.timedelta(days=3)
    _bars("AAPL", d0, [100, 101, 102, 103])  # d0..today

    s = _strategy(user)
    targets = []
    for i in range(3):
        t = PortfolioTarget.objects.create(
            strategy=s, as_of_date=d0 + dt.timedelta(days=i),
            status=PortfolioTarget.DONE, target_weights={"AAPL": 1.0},
        )
        cycle_mark.ensure_cycle_snapshot(t, force=True)  # what the cycle page / nightly does
        targets.append(t)

    pcts = [t.marked_snapshot["since_as_of_pct"] for t in targets]
    assert pcts == ["3.00", "1.98", "0.98"]  # each one is "as_of -> today", they overlap
    # …and chaining THOSE is what used to produce +6.07%:
    assert (1.03 * 1.0198 * 1.0098 - 1) * 100 == pytest.approx(6.07, abs=0.01)

    compute.recompute_strategies(today)
    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)

    # Disjoint intervals [d0,d1), [d1,d2), [d2,today] telescope to 103/100 - 1.
    assert sc.n_cycles == 3 and sc.n_observations == 3
    assert float(sc.total_return_pct) == pytest.approx(3.00, abs=0.01)
    # Three observations cannot support a ratio, so none is offered.
    assert sc.provisional is True
    assert sc.sharpe is None and sc.sortino is None
    assert sc.annualised_return_pct is None
    assert sc.metrics_version == 2


def test_annualisation_uses_observed_cadence_not_252(user):
    """A WEEKLY strategy annualises with ~52, not 252."""
    today = timezone.localdate()
    base = today - dt.timedelta(days=7 * 25)
    s = _strategy(user, name="weekly")
    # 26 weekly closes, alternating up/down so there is real dispersion.
    prices = []
    px = 100.0
    for i in range(26):
        px *= 1.01 if i % 3 else 0.99
        prices.append(round(px, 4))
    for i, close in enumerate(prices):
        DailyBar.objects.create(
            ticker="AAPL", date=base + dt.timedelta(days=7 * i),
            open=close, high=close, low=close, close=close, adjusted_close=close,
            volume=1000, source="fmp",
        )
    for i in range(25):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=7 * i),
            status=PortfolioTarget.DONE, target_weights={"AAPL": 1.0},
        )
    compute.recompute_strategies(today)
    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)
    assert sc.n_observations == 25
    assert sc.provisional is False
    # 365.25 / 7 ≈ 52.2 — the observed cadence, not the hard-coded 252.
    assert float(sc.periods_per_year) == pytest.approx(52.18, abs=0.1)


# ---------------------------------------------------------------------------
# F-overflow — a 6% mean "cycle return" no longer kills the nightly recompute
# ---------------------------------------------------------------------------


def test_recompute_strategies_survives_extreme_returns(user):
    """25 daily cycles compounding +6% each is what used to annualise to
    2.38e8 % and blow past ``NUMERIC(12,4)``.

    The INSERT raised ``numeric field overflow`` on Postgres *after*
    ``recompute_strategies`` had deleted the day's rows (leaderboard empty until
    the next night), and Django's read-side converter raised
    ``decimal.InvalidOperation`` so every leaderboard GET 500'd. Now every
    decimal is clamped to its column at the write boundary.
    """
    today = timezone.localdate()
    base = today - dt.timedelta(days=25)
    px = 100.0
    closes = []
    for _ in range(26):
        closes.append(round(px, 4))
        px *= 1.06
    _bars("AAPL", base, closes)
    s = _strategy(user)
    for i in range(25):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=i),
            status=PortfolioTarget.DONE, target_weights={"AAPL": 1.0},
        )

    compute.recompute_strategies(today)

    field = StrategyScorecard._meta.get_field("annualised_return_pct")
    assert (field.max_digits, field.decimal_places) == (12, 4)
    limit = 10 ** (field.max_digits - field.decimal_places)

    # The read side no longer raises: the value fits the column.
    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)
    assert sc.n_observations == 25 and sc.provisional is False
    assert sc.annualised_return_pct is not None
    assert abs(float(sc.annualised_return_pct)) < limit
    with connection.cursor() as cur:
        cur.execute("SELECT annualised_return_pct FROM leaderboard_strategyscorecard "
                    "WHERE strategy_id = %s AND window = 'lifetime'", [s.id])
        stored = cur.fetchone()[0]
    assert abs(float(stored)) < limit  # clamped, not 2.38e8

    c = APIClient(raise_request_exception=False)
    c.force_authenticate(user)
    assert c.get("/api/leaderboard/strategies/?window=lifetime").status_code == 200


# ---------------------------------------------------------------------------
# F-rerun-gaming
# ---------------------------------------------------------------------------


def test_thirty_reruns_of_one_call_are_one_decision(user):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])
    for _ in range(30):  # the same ticker, the same date, the same outcome
        _persona_run(user, "AAPL", "buffett", "bullish", 80, as_of)
    today = timezone.localdate()
    compute.recompute_agents(today)
    sc = AgentScorecard.objects.get(agent_name="buffett", window="lifetime", as_of=today)
    assert sc.n_decisions == 1
    assert sc.n_directional == 1              # ONE observation, not thirty
    assert sc.provisional is True             # and it stays provisional
    assert float(sc.hit_rate) == 1.0
    assert float(sc.hit_rate_ci_low) < 0.3    # a single sample says almost nothing


def test_overlapping_forward_windows_are_thinned(user):
    """Two calls on the same ticker three days apart share four of their five
    forward days — one observation of the market, not two."""
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100 + i for i in range(30)])
    _persona_run(user, "AAPL", "munger", "bullish", 70, as_of)
    _persona_run(user, "AAPL", "munger", "bullish", 70, as_of + dt.timedelta(days=3))
    _persona_run(user, "AAPL", "munger", "bullish", 70, as_of + dt.timedelta(days=14))
    today = timezone.localdate()
    compute.recompute_agents(today)
    sc = AgentScorecard.objects.get(agent_name="munger", window="lifetime", as_of=today)
    assert sc.n_directional == 2  # the +3d call overlaps and is dropped


# ---------------------------------------------------------------------------
# F-hindsight — a backdated as_of_date no longer lands in the 30d window
# ---------------------------------------------------------------------------


def test_backdated_run_is_not_scored_in_the_30d_window(user):
    as_of = dt.date(2020, 3, 16)  # 6+ years before today's run
    _bars("SPY", as_of, [240, 250, 260, 270, 280, 290])
    _persona_run(user, "SPY", "burry", "bullish", 90, as_of)  # created today, as_of 2020
    today = timezone.localdate()
    # Windows are on as_of_date now, so a 2020 decision is not "the last 30 days".
    assert compute._collect_agent_decisions(compute._cutoff(30, today), 5) == []
    compute.recompute_agents(today)
    assert not AgentScorecard.objects.filter(
        agent_name="burry", window="30d", as_of=today
    ).exists()
    # It is still visible over the lifetime window, where it belongs.
    lifetime = AgentScorecard.objects.get(
        agent_name="burry", window="lifetime", as_of=today
    )
    assert lifetime.n_directional == 1


# ---------------------------------------------------------------------------
# F-xtenant — the drill-down and the scorecards are per-owner
# ---------------------------------------------------------------------------


def test_agent_decisions_drilldown_is_single_tenant(user, other_user):
    as_of = dt.date(2026, 5, 1)
    _bars("NVDA", as_of, [100, 101, 102, 103, 104, 105])
    run = _persona_run(user, "NVDA", "wood", "bullish", 70, as_of, model="openrouter:secret/model")

    stranger = APIClient()
    stranger.force_authenticate(other_user)
    r = stranger.get("/api/leaderboard/agents/wood/decisions/?window=lifetime")
    assert r.status_code == 200
    assert r.json()["decisions"] == []  # no tickers, dates or model ids leak

    # The stranger may still trigger the (global) rebuild, but the counts they
    # get back — and the rows they can read — are their own, which is none.
    r2 = stranger.post("/api/leaderboard/recompute/")
    assert r2.status_code == 200 and r2.json()["agents"] == 0
    assert stranger.get("/api/leaderboard/agents/?window=lifetime").json()["rows"] == []

    owner = APIClient()
    owner.force_authenticate(user)
    mine = owner.get("/api/leaderboard/agents/wood/decisions/?window=lifetime").json()
    assert mine["decisions"][0]["run_id"] == run.id
    assert mine["decisions"][0]["ticker"] == "NVDA"
    assert owner.get("/api/leaderboard/agents/?window=lifetime").json()["rows"]


# ---------------------------------------------------------------------------
# F-nav-cash — council_net_value_usd is marked on equity, not the cash residual
# ---------------------------------------------------------------------------


def test_council_net_value_uses_equity_not_cash(user):
    s = _strategy(user, cash="10000")
    # The book holds $90,000 of stock at cost; equity ≈ $100,000, cash = $10,000.
    Position.objects.create(portfolio=s.portfolio, ticker="AAPL",
                            quantity=Decimal("900"), avg_cost=Decimal("100"))
    base = dt.date(2026, 1, 5)
    now = timezone.now().isoformat()
    for i in range(30):
        PortfolioTarget.objects.create(
            strategy=s, as_of_date=base + dt.timedelta(days=i), status=PortfolioTarget.DONE,
            target_weights={"AAPL": 0.9},
            marked_snapshot={"since_as_of_pct": "1.0", "snapshot_at": now},
            baseline_weights={"AAPL": 0.9},
            baseline_marked_snapshot={"since_as_of_pct": "0.0", "snapshot_at": now},
        )
    today = timezone.localdate()
    assert compute._strategy_nav(s.portfolio) == pytest.approx(100_000.0)
    compute.recompute_strategies(today)
    sc = StrategyScorecard.objects.get(strategy=s, window="lifetime", as_of=today)
    realised_total = 1.01 ** 30 - 1  # what the council-alpha leg compounds
    assert float(sc.council_net_value_usd) == pytest.approx(
        realised_total * 100_000, rel=1e-3
    )


# ---------------------------------------------------------------------------
# F-n+1 — the drill-down is one batched query, and it is bounded
# ---------------------------------------------------------------------------


def test_agent_decision_detail_query_count_is_constant(user):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])

    def _count(n):
        AgentMessage.objects.all().delete()
        Run.objects.all().delete()
        for _ in range(n):
            _persona_run(user, "AAPL", "graham", "bullish", 60, as_of)
        with CaptureQueriesContext(connection) as ctx:
            rows = compute.agent_decision_detail("graham", window="lifetime")
        assert len(rows) == n
        return len(ctx.captured_queries)

    q1, q20 = _count(1), _count(20)
    assert q20 == q1          # constant in the number of decisions
    assert q20 <= 3


def test_agent_decision_detail_is_paginated(user):
    as_of = dt.date(2026, 5, 1)
    _bars("AAPL", as_of, [100, 101, 102, 103, 104, 105])
    for _ in range(5):
        _persona_run(user, "AAPL", "graham", "bullish", 60, as_of)
    assert len(compute.agent_decision_detail("graham", window="lifetime", limit=2)) == 2
    c = APIClient()
    c.force_authenticate(user)
    body = c.get("/api/leaderboard/agents/graham/decisions/?window=lifetime&limit=2").json()
    assert body["limit"] == 2 and body["offset"] == 0
    assert len(body["decisions"]) == 2
    assert len(compute.agent_decision_detail(
        "graham", window="lifetime", limit=2, offset=4,
    )) == 1
