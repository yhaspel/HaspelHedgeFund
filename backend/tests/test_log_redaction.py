"""RedactSecretsFilter scrubs token-bearing query strings from log records."""
from __future__ import annotations

import logging

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
