"""P5-SH WS2.3 — optional Sentry stays off and unimported unless SENTRY_DSN is set."""
from __future__ import annotations

import sys

from hedgefund.observability import init_sentry


def test_sentry_dsn_unset_by_default(settings):
    assert getattr(settings, "SENTRY_DSN", "") == ""


def test_sentry_sdk_not_imported_when_dsn_unset():
    # Acceptance: SENTRY_DSN unset ⇒ no Sentry import/initialization on any path.
    # The test suite runs with SENTRY_DSN unset, so nothing should have loaded it.
    assert "sentry_sdk" not in sys.modules


def test_init_sentry_is_a_noop_for_empty_dsn():
    assert init_sentry("") is False
    assert "sentry_sdk" not in sys.modules
