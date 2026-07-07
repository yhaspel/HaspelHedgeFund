"""P5-SH WS2.1 — request_id / run_id log context.

ContextFilter stamps the bound contextvars onto every record; the JSON formatter
emits them as fields; RequestIdMiddleware binds request_id per HTTP request; and
execute_run binds run_id so every worker log line inside a run is greppable.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory
from pythonjsonlogger.jsonlogger import JsonFormatter

from hedgefund.logging_filters import ContextFilter, request_id_var, run_id_var
from hedgefund.middleware import RequestIdMiddleware


def _record(msg="hi"):
    return logging.LogRecord("t", logging.INFO, __file__, 0, msg, None, None)


def test_context_filter_injects_bound_ids():
    rec = _record()
    tok_r = request_id_var.set("req-123")
    tok_run = run_id_var.set("run-456")
    try:
        assert ContextFilter().filter(rec) is True
        assert rec.request_id == "req-123"
        assert rec.run_id == "run-456"
    finally:
        request_id_var.reset(tok_r)
        run_id_var.reset(tok_run)


def test_context_filter_defaults_to_empty_when_unset():
    rec = _record()
    ContextFilter().filter(rec)
    assert rec.request_id == ""
    assert rec.run_id == ""


def test_json_formatter_emits_the_ids():
    fmt = JsonFormatter("%(levelname)s %(request_id)s %(run_id)s %(message)s")
    rec = _record("hello")
    tok = request_id_var.set("req-abc")
    try:
        ContextFilter().filter(rec)
        out = json.loads(fmt.format(rec))
    finally:
        request_id_var.reset(tok)
    assert out["request_id"] == "req-abc"
    assert out["run_id"] == ""
    assert out["message"] == "hello"


def test_middleware_binds_request_id_and_resets_after():
    captured = {}

    def _get_response(request):
        captured["rid"] = request_id_var.get("")
        return HttpResponse("ok")

    resp = RequestIdMiddleware(_get_response)(RequestFactory().get("/"))
    assert captured["rid"], "request_id must be set while handling the request"
    assert resp["X-Request-ID"] == captured["rid"]
    assert request_id_var.get("") == "", "request_id must be reset after the request"


def test_middleware_honors_inbound_request_id_header():
    captured = {}

    def _get_response(request):
        captured["rid"] = request_id_var.get("")
        return HttpResponse("ok")

    req = RequestFactory().get("/", HTTP_X_REQUEST_ID="caller-supplied-123")
    resp = RequestIdMiddleware(_get_response)(req)
    assert captured["rid"] == "caller-supplied-123"
    assert resp["X-Request-ID"] == "caller-supplied-123"


@pytest.mark.django_db
def test_execute_run_binds_run_id_for_worker_logs():
    from apps.runs.models import Run
    from apps.runs.tasks import execute_run

    user = get_user_model().objects.create_user(email="ctx@example.test", password="x")
    run = Run.objects.create(
        user=user, tickers=["AAPL"], as_of_date=dt.date(2026, 6, 1),
        status=Run.QUEUED, max_budget_usd=Decimal("100"),
    )
    seen = {}

    def _capture(state):
        seen["run_id"] = run_id_var.get("")
        return {}

    graph = MagicMock()
    graph.invoke.side_effect = _capture
    with patch("apps.runs.tasks.resolve_graph", return_value=graph), \
         patch("apps.runs.tasks.get_fmp_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_edgar_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks.get_ownership_provider", return_value=MagicMock()), \
         patch("apps.runs.tasks._persist_outputs"):
        execute_run(run.id)

    assert seen["run_id"] == str(run.id)
