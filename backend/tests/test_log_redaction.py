"""RedactSecretsFilter scrubs token-bearing query strings from log records."""
from __future__ import annotations

import io
import json
import logging
import logging.config
import os
import subprocess
import sys

import httpx
import pytest
from celery import signals
from django.conf import settings

from hedgefund.logging_filters import RedactSecretsFilter


def _record(msg: str, args: tuple | dict | None = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg=msg, args=args, exc_info=None,
    )


def test_redacts_token_in_url():
    rec = _record("GET https://api.tiingo.com/x?tickers=AAPL&token=SUPERSECRET&limit=10")
    RedactSecretsFilter().filter(rec)
    assert "SUPERSECRET" not in rec.getMessage()
    assert "token=REDACTED" in rec.getMessage()
    assert "tickers=AAPL" in rec.getMessage()


def test_redacts_apikey_and_access_token():
    rec = _record("call https://x.com/?apikey=AAA&access_token=BBB")
    RedactSecretsFilter().filter(rec)
    m = rec.getMessage()
    assert "AAA" not in m
    assert "BBB" not in m
    assert m.count("REDACTED") == 2


def test_redacts_in_args_tuple():
    rec = _record("hit %s", ("https://x/?token=SECRET&other=keep",))
    RedactSecretsFilter().filter(rec)
    assert "SECRET" not in rec.getMessage()
    assert "other=keep" in rec.getMessage()


def test_leaves_clean_messages_alone():
    rec = _record("nothing to scrub here")
    RedactSecretsFilter().filter(rec)
    assert rec.getMessage() == "nothing to scrub here"


# --- formatted-message scrubbing (non-str args, tracebacks, Celery, httpx) ----

FMP_URL = "https://financialmodelingprep.com/api/v3/quote/AAPL?apikey=FMP-SECRET-TEST"


def _status_error(url: str = FMP_URL) -> httpx.HTTPStatusError:
    try:
        httpx.Response(429, request=httpx.Request("GET", url)).raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("raise_for_status() did not raise")


@pytest.fixture
def log_buffer():
    """The settings.LOGGING chain (json formatter + context + redact filters, and
    the httpx logger) writing into a buffer; the stream is restored afterwards."""
    logging.config.dictConfig(json.loads(json.dumps(settings.LOGGING)))
    handler = logging.getLogger().handlers[0]
    old_stream, buf = handler.stream, io.StringIO()
    handler.stream = buf
    try:
        yield buf
    finally:
        handler.stream = old_stream


def test_status_error_logged_with_log_exception_is_scrubbed(log_buffer):
    log = logging.getLogger("apps.data.providers.market_news")
    try:
        httpx.Response(429, request=httpx.Request("GET", FMP_URL)).raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.exception("market_news provider failure provider=%s err=%s", "fmp", exc)
    out = log_buffer.getvalue()
    assert "FMP-SECRET-TEST" not in out
    assert "apikey=REDACTED" in out
    assert "Traceback" in out  # the traceback is still emitted, just scrubbed


def test_status_error_passed_as_a_plain_arg_is_scrubbed(log_buffer):
    # apps/screener/pipeline.py-style: the exception object itself is the %s arg.
    logging.getLogger("apps.screener.pipeline").error("%s", _status_error())
    out = log_buffer.getvalue()
    assert "FMP-SECRET-TEST" not in out
    assert "apikey=REDACTED" in out


def test_non_str_args_are_scrubbed_and_numeric_formatting_survives():
    rec = _record(
        "attempt %d/%d failed: %s (%.1fs)",
        (2, 5, _status_error("https://api.tiingo.com/x?token=TIINGO-SECRET"), 1.5),
    )
    RedactSecretsFilter().filter(rec)
    msg = rec.getMessage()
    assert msg.startswith("attempt 2/5 failed: Client error '429 Too Many Requests'")
    assert msg.endswith("(1.5s)")
    assert "TIINGO-SECRET" not in msg
    assert rec.args == ()


def test_second_pass_is_idempotent_and_keeps_literal_percent():
    rec = _record("100%% of %s", ("https://x.test/?apikey=SECRET&n=1",))
    f = RedactSecretsFilter()
    f.filter(rec)
    f.filter(rec)
    assert rec.getMessage() == "100% of https://x.test/?apikey=REDACTED&n=1"


def test_mismatched_format_args_do_not_raise_and_are_scrubbed():
    rec = _record("x=%s y=%s", ("https://x.test/?api_key=SECRET",))
    assert RedactSecretsFilter().filter(rec) is True
    assert "SECRET" not in rec.getMessage()
    assert "api_key=REDACTED" in rec.getMessage()


def test_redacts_key_param_bearer_tokens_and_provider_keys():
    rec = _record(
        "GET %s headers=%s or=%s ant=%s",
        (
            httpx.URL("https://maps.example.test/geo?key=GOOGLE-SECRET&q=x"),
            {"Authorization": "Bearer RESEND-BEARER-SECRET"},
            "sk-or-v1-0123456789abcdef",
            "sk-ant-api03-ANTHROPIC_SECRET-xyz",
        ),
    )
    RedactSecretsFilter().filter(rec)
    msg = rec.getMessage()
    for secret in ("GOOGLE-SECRET", "RESEND-BEARER-SECRET", "0123456789abcdef", "ANTHROPIC"):
        assert secret not in msg
    assert "key=REDACTED&q=x" in msg
    assert "Bearer REDACTED" in msg
    assert "sk-or-v1-REDACTED" in msg and "sk-ant-REDACTED" in msg


def test_key_param_rule_does_not_eat_longer_names():
    rec = _record("cache_key=abc monkey=banana")
    RedactSecretsFilter().filter(rec)
    assert rec.getMessage() == "cache_key=abc monkey=banana"


def test_httpx_request_line_is_scrubbed_at_the_logger(log_buffer):
    # httpx logs `HTTP Request: GET <httpx.URL> "HTTP/1.1 200 OK"` with a non-str
    # URL arg. The logger-level filter scrubs it for ANY handler, even a bare one.
    httpx_log = logging.getLogger("httpx")
    bare_buf = io.StringIO()
    bare = logging.StreamHandler(bare_buf)
    old_level = httpx_log.level
    httpx_log.addHandler(bare)
    httpx_log.setLevel(logging.INFO)
    try:
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        with httpx.Client(transport=transport) as client:
            client.get("https://api.tiingo.com/tiingo/daily/AAPL?token=TIINGO-SECRET")
    finally:
        httpx_log.setLevel(old_level)
        httpx_log.removeHandler(bare)
    for out in (log_buffer.getvalue(), bare_buf.getvalue()):
        assert "HTTP Request: GET" in out
        assert "TIINGO-SECRET" not in out
        assert "token=REDACTED" in out


def test_celery_logging_signals_attach_the_filter_once():
    import hedgefund.celery  # noqa: F401 - connects the signal receivers

    logger = logging.getLogger("tests.celery_redaction")
    handler = logging.StreamHandler(io.StringIO())
    logger.addHandler(handler)
    try:
        for signal in (signals.after_setup_logger, signals.after_setup_task_logger):
            for _ in range(2):  # a re-fired signal must not stack filters
                signal.send(
                    sender=None, logger=logger, loglevel=logging.INFO,
                    logfile=None, format="", colorize=False,
                )
        assert sum(isinstance(f, RedactSecretsFilter) for f in handler.filters) == 1
    finally:
        logger.removeHandler(handler)


_CELERY_PROBE = """
import logging
import django
django.setup()
import httpx
from hedgefund.celery import app
from hedgefund.logging_filters import RedactSecretsFilter
app.log.setup(loglevel="INFO")  # what `celery worker` / `celery beat` run at startup
handlers = logging.getLogger().handlers + logging.getLogger("celery.task").handlers
ok = bool(handlers) and all(
    any(isinstance(f, RedactSecretsFilter) for f in h.filters) for h in handlers
)
print(f"FILTERED={ok}")
url = httpx.URL("https://x.test/q?apikey=PROBE-SECRET")
logging.getLogger("apps.probe").warning("GET %s", url)
logging.getLogger("celery.task").warning("task GET %s", url)
"""


def test_celery_worker_and_beat_handlers_redact():
    # A fresh interpreter: Celery's setup hijacks the root logger, which must not
    # be done to the test process itself.
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": settings.SETTINGS_MODULE}
    result = subprocess.run(
        [sys.executable, "-c", _CELERY_PROBE],
        capture_output=True, text=True, env=env, cwd=settings.BASE_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert "FILTERED=True" in result.stdout
    assert "PROBE-SECRET" not in result.stderr
    assert result.stderr.count("apikey=REDACTED") == 2
