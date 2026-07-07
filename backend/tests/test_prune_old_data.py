"""P5-SH WS3.2 — retention command: dry-run reports, --commit deletes only past age."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _seed():
    from apps.data.models import NewsItem
    from hedgefund_agents.models import LLMCall

    now = timezone.now()
    old = now - dt.timedelta(days=200)

    fresh_call = LLMCall.objects.create(agent_name="a", provider="p", model="m",
                                        cost_usd=Decimal("0.1"))
    old_call = LLMCall.objects.create(agent_name="a", provider="p", model="m",
                                      cost_usd=Decimal("0.1"))
    LLMCall.objects.filter(pk=old_call.pk).update(created_at=old)

    NewsItem.objects.create(ticker="AAPL", published_at=now, headline="fresh",
                            source="s", provider="tiingo", url="https://x/1")
    NewsItem.objects.create(ticker="AAPL", published_at=old, headline="old",
                            source="s", provider="tiingo", url="https://x/2")
    return fresh_call.pk, old_call.pk


def _run(*args) -> str:
    out = StringIO()
    call_command("prune_old_data", *args, stdout=out)
    return out.getvalue()


def test_dry_run_reports_candidates_and_deletes_nothing():
    from apps.data.models import NewsItem
    from hedgefund_agents.models import LLMCall

    _seed()
    out = _run("--older-than-days", "90", "--llm-calls", "--news")
    assert "DRY-RUN" in out
    assert "LLMCall: 1 candidate" in out
    assert "NewsItem: 1 candidate" in out
    # Nothing deleted.
    assert LLMCall.objects.count() == 2
    assert NewsItem.objects.count() == 2


def test_commit_prunes_only_rows_past_age():
    from apps.data.models import NewsItem
    from hedgefund_agents.models import LLMCall

    fresh_call_pk, old_call_pk = _seed()
    out = _run("--older-than-days", "90", "--llm-calls", "--news", "--commit")
    assert "deleted 1 row" in out
    assert list(LLMCall.objects.values_list("pk", flat=True)) == [fresh_call_pk]
    assert LLMCall.objects.filter(pk=old_call_pk).count() == 0
    assert NewsItem.objects.filter(headline="old").count() == 0
    assert NewsItem.objects.filter(headline="fresh").count() == 1


def test_requires_a_target():
    with pytest.raises(CommandError):
        _run("--older-than-days", "90")


def test_rejects_nonpositive_age():
    with pytest.raises(CommandError):
        _run("--older-than-days", "0", "--llm-calls")
