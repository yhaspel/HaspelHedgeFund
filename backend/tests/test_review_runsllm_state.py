"""Adversarial review (reviewer: runsllm) — run state machine / graph-state proofs."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.runs.models import AgentMessage, Decision, Run
from hedgefund_agents.llm.client import LLMResponse
from hedgefund_agents.outputs import PersonaOutput, RiskOutput

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="state@example.test", password="supersecret")


def _run(user, **kw):
    defaults = dict(tickers=["AAPL"], as_of_date=dt.date(2026, 6, 1), status=Run.QUEUED)
    defaults.update(kw)
    return Run.objects.create(user=user, **defaults)


def _patched_execute(run_id: int, final_state: dict | None = None):
    """Run execute_run with the graph + providers stubbed."""
    from apps.runs.tasks import execute_run

    graph = MagicMock()
    graph.invoke.return_value = final_state or {}
    with patch("apps.runs.tasks.resolve_graph", return_value=graph), \
         patch("apps.runs.tasks.get_fmp_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_edgar_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_ownership_provider", return_value=MagicMock()):
        execute_run(run_id)
    return graph


# ---------------------------------------------------------------------------
# F-D (FIXED — WP-B2): persona_evolution survives the StateGraph boundary
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_persona_evolution_never_reaches_persona_prompt_through_graph(monkeypatch):
    """execute_run puts `persona_evolution` into the initial state. AgentState
    (base.py) now declares that key, so StateGraph(AgentState) carries it to
    the persona nodes exactly like the declared `investor_profile` block
    (control) — the "Evolved" badge written to Run.persona_evolution_applied
    is truthful."""
    from hedgefund_agents.graphs import council
    from hedgefund_agents.personas.buffett import run_buffett

    prompts: list[str] = []

    class _Fake:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
            sys_text = " ".join(m.content for m in messages if m.role == "system").lower()
            user_text = " ".join(m.content for m in messages if m.role == "user")
            if "risk manager" in sys_text:
                text = RiskOutput(rationale="ok", max_position_pct_for_this_trade=0.1
                                  ).model_dump_json()
            else:
                prompts.append(user_text)
                text = PersonaOutput(signal="neutral", confidence=50, thesis="t",
                                     key_risks=[]).model_dump_json()
            return LLMResponse(text=text, model=model, provider="fake")

    # Analytical nodes stubbed so only the persona + risk LLM calls happen.
    monkeypatch.setattr(council, "ANALYTICAL_NODES", {
        "fundamentals": lambda s: {"fundamentals": {}}, "technicals": lambda s: {"technicals": {}},
        "valuation": lambda s: {"valuation": {}}, "sentiment": lambda s: {"sentiment": {}},
        "macro": lambda s: {"macro": {}}, "news_digest": lambda s: {"news_digest": {}},
    })

    class _NoFilings:
        def get_recent_filings(self, *a, **k):
            return []

    base_state = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": None,
        "filings_provider": _NoFilings(), "disable_cio": True,
        "investor_profile": {"agent_brief": "PROFILE-MARKER-123"},
        "persona_evolution": {"buffett": "EVOLUTION-MARKER-XYZ"},
    }
    with patch("hedgefund_agents.personas._base.get_llm", return_value=_Fake()), \
         patch("hedgefund_agents.risk.risk_manager.get_llm", return_value=_Fake()), \
         patch("hedgefund_agents.personas._base.record_llm_call"), \
         patch("hedgefund_agents.risk.risk_manager.record_llm_call"):
        # Control: the node called directly DOES inject the block.
        run_buffett(dict(base_state))
        assert "EVOLUTION-MARKER-XYZ" in prompts[-1]
        assert "PROFILE-MARKER-123" in prompts[-1]
        prompts.clear()
        # Through the compiled council graph (what execute_run uses):
        council.build_council_graph(personas=["buffett"]).invoke(dict(base_state))
    assert len(prompts) == 1
    assert "PROFILE-MARKER-123" in prompts[0]        # declared key survives
    assert "EVOLUTION-MARKER-XYZ" in prompts[0]      # now declared → survives too


# ---------------------------------------------------------------------------
# F-E: execute_run ignores terminal status; orphan sweep false positives
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_execute_run_reexecutes_cancelled_run_and_flips_it_to_done(user):
    """FIXED (WP-B2): execute_run CLAIMS the run with a conditional
    QUEUED/RUNNING → RUNNING update, so a cancelled run is never resurrected
    (and never spends again)."""
    run = _run(user, status=Run.CANCELLED, error_message="Cancelled by user.",
               finished_at=timezone.now())
    graph = _patched_execute(run.id)
    assert not graph.invoke.called                # the council never ran
    run.refresh_from_db()
    assert run.status == Run.CANCELLED            # the user's cancel stands


@pytest.mark.django_db
def test_cancelling_mid_run_is_not_overwritten_by_the_done_transition(user):
    """RUNNING→DONE is conditional too: a cancel that lands while the council
    is mid-flight wins."""
    from apps.runs.tasks import execute_run

    run = _run(user)

    def _cancel_then_return(_state):
        Run.objects.filter(pk=run.pk).update(status=Run.CANCELLED)
        return {}

    graph = MagicMock()
    graph.invoke.side_effect = _cancel_then_return
    with patch("apps.runs.tasks.resolve_graph", return_value=graph), \
         patch("apps.runs.tasks.get_fmp_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_edgar_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_ownership_provider", return_value=MagicMock()):
        execute_run(run.id)
    run.refresh_from_db()
    assert run.status == Run.CANCELLED


@pytest.mark.django_db
def test_execute_run_reexecutes_failed_run_and_duplicates_persisted_rows(user):
    """FIXED (WP-B2): the same claim gives idempotency. A second execution
    (after the orphan sweep marked it FAILED, or a manual re-fire) is refused,
    so no second set of AgentMessage/Decision rows is appended."""
    final = {"decision": {"ticker": "AAPL", "action": "hold", "aggregate_confidence": 0,
                          "rationale": "r", "dissenting_personas": []},
             "risk": {"veto": False}}
    run = _run(user)
    _patched_execute(run.id, final)
    _patched_execute(run.id, final)
    assert Decision.objects.filter(run=run).count() == 1
    assert AgentMessage.objects.filter(run=run, agent_name="risk").count() == 1
    run.refresh_from_db()
    assert run.status == Run.DONE


class _Inspect:
    def __init__(self, active):
        self._active = active

    def active(self):
        return self._active


@pytest.mark.django_db
def test_orphan_sweep_kills_queued_and_sync_scheduled_runs(user):
    """FIXED (WP-B2): a run that is merely QUEUED behind others has not started
    (so "no active task" proves nothing), and every child of the SYNCHRONOUS
    scheduled fan-out runs in-process with celery_task_id == '' (so the broker
    knows nothing about it). Neither is swept any more."""
    from apps.runs.tasks import sweep_orphan_runs

    old = timezone.now() - dt.timedelta(minutes=60)
    queued = _run(user, status=Run.QUEUED)
    sync_child = _run(user, status=Run.RUNNING)            # schedules.tasks child
    Run.objects.filter(pk__in=[queued.pk, sync_child.pk]).update(created_at=old)

    with patch("hedgefund.celery.app.control.inspect",
               return_value=_Inspect({"worker1": []})), \
         patch("apps.notifications.operator.notify_orphan_sweep") as alert:
        out = sweep_orphan_runs()
    assert out["swept"] == 0
    for r in (queued, sync_child):
        r.refresh_from_db()
        assert r.status in Run.ACTIVE_STATUSES
    assert not alert.called                                # no false "worker died" alert


@pytest.mark.django_db
def test_orphan_sweep_still_catches_a_genuinely_dead_run(user):
    """The sweeper must keep doing its actual job: a Celery-dispatched RUNNING
    run, older than the task time limit, that no worker is executing."""
    from apps.runs.tasks import sweep_orphan_runs

    dead = _run(user, status=Run.RUNNING, celery_task_id="task-dead")
    Run.objects.filter(pk=dead.pk).update(
        created_at=timezone.now() - dt.timedelta(minutes=60)
    )
    with patch("hedgefund.celery.app.control.inspect",
               return_value=_Inspect({"worker1": [{"id": "other-task"}]})), \
         patch("apps.notifications.operator.notify_orphan_sweep") as alert:
        out = sweep_orphan_runs()
    assert out["swept"] == 1
    dead.refresh_from_db()
    assert dead.status == Run.FAILED and dead.error_message.startswith("Orphaned")
    assert alert.called


@pytest.mark.django_db
def test_orphan_sweep_skips_a_run_still_inside_the_task_time_limit(user, settings):
    """created_at is the QUEUE time, so the window must cover the task's own
    wall-clock ceiling — a run legitimately executing up to
    RUN_HARD_TIME_LIMIT_SECONDS has not been abandoned."""
    from apps.runs.tasks import sweep_orphan_runs

    settings.RUN_HARD_TIME_LIMIT_SECONDS = 3600     # 60 min
    live = _run(user, status=Run.RUNNING, celery_task_id="task-abc")
    Run.objects.filter(pk=live.pk).update(
        created_at=timezone.now() - dt.timedelta(minutes=20)
    )
    with patch("hedgefund.celery.app.control.inspect",
               return_value=_Inspect({"worker1": []})), \
         patch("apps.notifications.operator.notify_orphan_sweep"):
        assert sweep_orphan_runs()["swept"] == 0
    live.refresh_from_db()
    assert live.status == Run.RUNNING


@pytest.mark.django_db
def test_orphan_sweep_treats_no_worker_reply_as_no_active_tasks(user):
    """FIXED (WP-B2): inspect.active() returns None when no worker replied
    within 2s (busy broker / slow worker). `(None or {})` used to read that as
    "nothing is running" and fail every live run; now it aborts the sweep."""
    from apps.runs.tasks import sweep_orphan_runs

    live = _run(user, status=Run.RUNNING, celery_task_id="task-abc")
    Run.objects.filter(pk=live.pk).update(created_at=timezone.now() - dt.timedelta(minutes=60))
    with patch("hedgefund.celery.app.control.inspect", return_value=_Inspect(None)), \
         patch("apps.notifications.operator.notify_orphan_sweep"):
        out = sweep_orphan_runs()
    assert out == {"swept": 0, "error": "no_worker_reply"}
    live.refresh_from_db()
    assert live.status == Run.RUNNING


# ---------------------------------------------------------------------------
# F-N: rerun does not copy max_budget_usd / graph_version
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_rerun_drops_budget_cap_and_graph_version(user):
    from apps.graphs.models import AgentGraph, AgentGraphVersion

    graph = AgentGraph.objects.create(user=user, name="g")
    version = AgentGraphVersion.objects.create(
        graph=graph, version=1, nodes=[{"type": "technicals"}, {"type": "buffett"}],
        validation_status=AgentGraphVersion.VALID,
    )
    original = _run(user, status=Run.FAILED, max_budget_usd=Decimal("1.50"),
                    graph_version=version, personas=["buffett"],
                    model_overrides={"buffett": "openrouter:x"})

    c = APIClient()
    token = c.post(reverse("login"), {"email": user.email, "password": "supersecret"},
                   format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("apps.runs.views.execute_run.delay") as d:
        d.return_value = MagicMock(id="t1")
        resp = c.post(reverse("run-rerun", args=[original.pk]), format="json")
    assert resp.status_code == 201
    new = Run.objects.get(pk=resp.data["id"])
    # FIXED (WP-B2): a rerun reproduces the original run, cap and graph included.
    assert new.max_budget_usd == Decimal("1.50")
    assert new.graph_version_id == version.pk
    assert new.model_overrides == original.model_overrides


# ---------------------------------------------------------------------------
# F-Q: max_budget_usd accepts nonsensical values
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("value", ["-5", "0", "1001"])
def test_run_create_accepts_negative_budget(user, value):
    """FIXED (WP-B2): max_budget_usd must be > 0 and <= 1000. A negative/zero
    cap was stored verbatim and then compared as `spent >= cap`, aborting the
    run on its first recorded LLM call."""
    c = APIClient()
    token = c.post(reverse("login"), {"email": user.email, "password": "supersecret"},
                   format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("apps.runs.views.execute_run.delay") as d:
        d.return_value = MagicMock(id="t1")
        resp = c.post(reverse("run-list-create"),
                      {"tickers": ["AAPL"], "as_of_date": "2026-06-01",
                       "max_budget_usd": value},
                      format="json")
    assert resp.status_code == 400
    assert "max_budget_usd" in resp.data
    assert not Run.objects.exists()


@pytest.mark.django_db
def test_run_create_accepts_a_sane_budget(user):
    c = APIClient()
    c.force_authenticate(user)
    with patch("apps.runs.views.execute_run.delay") as d:
        d.return_value = MagicMock(id="t1")
        resp = c.post(reverse("run-list-create"),
                      {"tickers": ["AAPL"], "as_of_date": "2026-06-01",
                       "max_budget_usd": "1.50"},
                      format="json")
    assert resp.status_code == 201
    assert Run.objects.get(pk=resp.data["id"]).max_budget_usd == Decimal("1.50")


# ---------------------------------------------------------------------------
# F-low: unhandled OverflowError → 500 on the run list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("q", ["?days=99999999999", "?portfolio_target=99999999999999999999"])
def test_run_list_query_params_overflow_to_500(user, q):
    """FIXED (WP-B2): out-of-range window/id params are clamped instead of
    overflowing timedelta / the integer key and surfacing as an HTTP 500."""
    c = APIClient()
    c.force_authenticate(user)
    resp = c.get(reverse("run-list-create") + q)
    assert resp.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("q,field", [
    ("?page=abc", "page"), ("?page=0", "page"), ("?page=-1", "page"),
    ("?page_size=abc", "page_size"), ("?page_size=0", "page_size"),
])
def test_run_list_rejects_malformed_pagination_params(user, q, field):
    c = APIClient()
    c.force_authenticate(user)
    resp = c.get(reverse("run-list-create") + q)
    assert resp.status_code == 400
    assert field in resp.data


@pytest.mark.django_db
def test_run_list_page_size_is_capped_at_100(user):
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    from apps.runs.views import RunsPagination

    _run(user)
    c = APIClient()
    c.force_authenticate(user)
    assert c.get(reverse("run-list-create") + "?page_size=5000").status_code == 200
    req = Request(APIRequestFactory().get("/api/runs/", {"page_size": "5000"}))
    assert RunsPagination().get_page_size(req) == 100
    assert RunsPagination().get_page_size(
        Request(APIRequestFactory().get("/api/runs/", {"page_size": "20"}))
    ) == 20


# ---------------------------------------------------------------------------
# F-D corollary (FIXED — WP-B2): portfolio_target_id survives → cycle cost attributed
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_portfolio_target_id_dropped_by_graph_so_cycle_cost_is_never_attributed(monkeypatch):
    """apps/portfolios/tasks.py puts `portfolio_target_id` in the council's
    initial state so every LLMCall links to the PortfolioTarget (cycle-view
    cost rollup + leaderboard cost). AgentState now declares the key, so
    every node's `state.get("portfolio_target_id")` is the real id."""
    from hedgefund_agents.graphs import council

    seen: list[dict] = []

    class _Fake:
        provider = "fake"

        def complete(self, *, model, messages, max_tokens=2048, temperature=0.2, json_mode=False):
            sys_text = " ".join(m.content for m in messages if m.role == "system").lower()
            if "risk manager" in sys_text:
                text = RiskOutput(rationale="ok", max_position_pct_for_this_trade=0.1
                                  ).model_dump_json()
            else:
                text = PersonaOutput(signal="neutral", confidence=50, thesis="t",
                                     key_risks=[]).model_dump_json()
            return LLMResponse(text=text, model=model, provider="fake")

    monkeypatch.setattr(council, "ANALYTICAL_NODES", {
        "fundamentals": lambda s: {"fundamentals": {}}, "technicals": lambda s: {"technicals": {}},
        "valuation": lambda s: {"valuation": {}}, "sentiment": lambda s: {"sentiment": {}},
        "macro": lambda s: {"macro": {}}, "news_digest": lambda s: {"news_digest": {}},
    })

    class _NoFilings:
        def get_recent_filings(self, *a, **k):
            return []

    def _rec(**kw):
        seen.append(kw)

    state = {"ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": 7,
             "portfolio_target_id": 42, "filings_provider": _NoFilings(), "disable_cio": True}
    with patch("hedgefund_agents.personas._base.get_llm", return_value=_Fake()), \
         patch("hedgefund_agents.risk.risk_manager.get_llm", return_value=_Fake()), \
         patch("hedgefund_agents.personas._base.record_llm_call", side_effect=_rec), \
         patch("hedgefund_agents.risk.risk_manager.record_llm_call", side_effect=_rec):
        council.build_council_graph(personas=["buffett"]).invoke(state)
    assert {kw["agent_name"] for kw in seen} == {"buffett", "risk_manager"}
    assert all(kw["run_id"] == 7 for kw in seen)                    # declared key survives
    assert all(kw["portfolio_target_id"] == 42 for kw in seen)      # now declared → survives


# ---------------------------------------------------------------------------
# F-D regression guard: every key the runners write into the initial state
# must be declared on AgentState (else StateGraph silently drops it).
# ---------------------------------------------------------------------------

def _initial_state_keys(func) -> set[str]:
    """Extract the literal keys of the `initial_state = {...}` dict in `func`."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "initial_state"
            and isinstance(node.value, ast.Dict)
        ):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "initial_state"
            and isinstance(node.value, ast.Dict)
        ):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"no literal initial_state dict in {func.__name__}")


def test_every_runner_initial_state_key_is_declared_on_agent_state():
    from apps.portfolios import tasks as ptasks
    from apps.runs import tasks as rtasks
    from hedgefund_agents.base import AgentState

    declared = set(AgentState.__annotations__)
    written = _initial_state_keys(rtasks.execute_run) | _initial_state_keys(
        ptasks.run_candidate_council
    )
    assert written, "no initial_state keys found"
    assert written <= declared, f"undeclared AgentState keys: {sorted(written - declared)}"


def test_initial_state_keys_survive_one_graph_hop():
    """A minimal StateGraph(AgentState) with one no-op node: every key of a
    runner-shaped input must come out the other side."""
    from langgraph.graph import END, StateGraph

    from hedgefund_agents.base import AgentState

    seen: dict = {}

    def probe(state):
        seen.update(state)
        return {}

    g = StateGraph(AgentState)
    g.add_node("probe", probe)
    g.set_entry_point("probe")
    g.add_edge("probe", END)
    payload = {
        "ticker": "AAPL", "as_of_date": dt.date(2026, 6, 1), "run_id": 1, "user_id": 2,
        "model_overrides": {}, "data_provider": object(), "filings_provider": object(),
        "ownership_provider": object(), "investor_profile": {"agent_brief": "p"},
        "persona_evolution": {"buffett": "e"}, "portfolio_target_id": 42,
        "borrow_veto": True, "pm_config": {"short_side": True}, "flavor": "sector_rotation",
        "sector": "Tech", "theme": "AI", "disable_cio": True,
    }
    g.compile().invoke(dict(payload))
    missing = [k for k in payload if k not in seen]
    assert not missing, f"keys dropped at the StateGraph boundary: {missing}"
